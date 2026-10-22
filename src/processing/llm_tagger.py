"""Use an LLM to generate structured tags for each entity (batched, parallel, resumable).

Uses the shared llm_client (google-genai, global rate limiter, 429 retry). Tags are generated
from ALL source descriptions of an entity, not just one.
"""
import argparse
import hashlib
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog

from src.processing import field_extractors as fx
from src.processing import llm_client

logger = structlog.get_logger()

# === TAXONOMY (with definitions so the LLM does not have to guess what a tag means) ===
MUSIC_GENRES = {
    "nhac_trinh": "nhạc Trịnh Công Sơn / nhạc Trịnh",
    "acoustic": "nhạc acoustic, guitar mộc",
    "jazz": "nhạc jazz",
    "bolero": "nhạc bolero, nhạc vàng, tiền chiến",
    "lofi_chill": "nhạc lofi, chill, nhẹ nhàng thư giãn",
    "indie": "nhạc indie, nhạc underground, band tự do",
    "traditional": "nhạc dân gian, cổ truyền, dân tộc",
    "piano": "nhạc piano, độc tấu piano",
    "pop_ballad": "nhạc pop, ballad, nhạc trẻ",
}
ATMOSPHERE_TAGS = {
    "yen_tinh": "yên tĩnh, ít ồn, phù hợp trò chuyện/làm việc",
    "lang_man": "lãng mạn, phù hợp hẹn hò",
    "co_kinh": "cổ kính, hoài cổ, nhà cũ, kiến trúc xưa, phong cách retro/vintage",
    "view_dep": "có view đẹp (hồ, phố, thành phố...)",
    "san_vuon": "có sân vườn, không gian xanh ngoài trời",
    "rooftop": "sân thượng, tầng thượng",
    "sang_trong": "sang trọng, cao cấp",
    "binh_dan": "bình dân, giá rẻ, gần gũi",
    "tre_trung": "trẻ trung, hiện đại, năng động",
    "co_dien": "phong cách cổ điển (classic), châu Âu, trang nhã",
}
FEATURES = {
    "co_live_music": "có biểu diễn nhạc sống, sân khấu, ca sĩ/ban nhạc hát trực tiếp",
    "cho_dat_cho": "nhận đặt chỗ / đặt bàn trước",
    "do_an_nhe": "có đồ ăn nhẹ, bánh, snack, món ăn kèm",
    "parking": "có chỗ để xe / bãi đỗ xe",
    "khong_thuoc_la": "không hút thuốc / khu vực cấm thuốc",
    "wifi_manh": "wifi mạnh, ổn định",
}

# Tags that mean the same in practice. Applied AFTER the LLM answer (the cache keeps the raw
# answer), so merging does not need a new Gemini call.
TAG_ALIASES = {"co_dien": "co_kinh"}


def _taxonomy_block(title: str, tags: Dict[str, str]) -> str:
    lines = "\n".join(f"  - {k}: {v}" for k, v in tags.items())
    return f"{title}:\n{lines}"


TAXONOMY_PROMPT = "\n".join([
    _taxonomy_block("music_genres", MUSIC_GENRES),
    _taxonomy_block("atmosphere_tags", ATMOSPHERE_TAGS),
    _taxonomy_block("features", FEATURES),
])

PROMPT_VERSION = "v2"
MIN_TEXT_LEN = 30
MAX_TEXT_CHARS = 4000  # safety cap on the combined descriptions of one entity
EMPTY_TAGS = {"music_genres": [], "atmosphere_tags": [], "features": []}


def entity_text(entity: Dict[str, Any]) -> str:
    """All distinct source descriptions of the entity, joined (falls back to `description`)."""
    seen: List[str] = []
    for d in entity.get("descriptions") or []:
        text = (d.get("text") if isinstance(d, dict) else str(d) or "").strip()
        if text and text not in seen:
            seen.append(text)
    if not seen and (entity.get("description") or "").strip():
        seen = [entity["description"].strip()]
    return "\n".join(seen)[:MAX_TEXT_CHARS]


def entity_key(entity: Dict[str, Any]) -> str:
    """Cache key: a changed name/text/prompt version is tagged again."""
    raw = f"{PROMPT_VERSION}|{entity.get('name', '')}|{entity_text(entity)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def clean_tags(raw: Dict[str, Any]) -> Dict[str, List[str]]:
    """Keep only valid taxonomy tags, de-duplicated, in order."""

    def keep(values: Any, allowed: Dict[str, str]) -> List[str]:
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(v for v in values if v in allowed))

    return {
        "music_genres": keep(raw.get("music_genres"), MUSIC_GENRES),
        "atmosphere_tags": keep(raw.get("atmosphere_tags"), ATMOSPHERE_TAGS),
        "features": keep(raw.get("features"), FEATURES),
    }


# A tag is kept only if the description contains evidence for it. Patterns run on text that is
# lower-cased and accent-stripped (so "trình" and "trịnh" both become "trinh": write patterns
# with that in mind, and use \b where a short word could sit inside another one,
# e.g. "xua" inside "xuat", "view" inside "review").
# Small LLMs tend to add plausible-sounding tags that no source mentions.
EVIDENCE = {
    "music_genres": {
        # "trinh" alone also matches "chuong trinh" / "trinh dien" -> need the musical context
        "nhac_trinh": r"nhac trinh(?! dien)|trinh cong son|nhac si trinh|ca khuc (?:cua )?trinh|\btcs\b",
        "acoustic": r"acoustic|guitar",
        "jazz": r"jazz",
        "bolero": r"bolero|nhac vang|tien chien",
        "lofi_chill": r"lofi|lo-fi|chill",
        "indie": r"indie|underground",
        "traditional": r"dan gian|co truyen|dan toc|truyen thong|dan ca|ca tru|cheo|quan ho",
        "piano": r"piano",
        "pop_ballad": r"\bpop\b|ballad|nhac tre|nhac tinh cam",
    },
    "atmosphere_tags": {
        "yen_tinh": r"yen tinh|yen binh|an yen|thanh tinh|\bit on\b|nhe nhang|thu gian|thu thai"
                    r"|quiet|peaceful|calm|tranquil",
        "lang_man": r"lang man|hen ho|cap doi|romantic",
        "co_kinh": r"co kinh|hoai co|hoai niem|\bxua\b|xua cu|co xua|nha co|co dien|chau au"
                   r"|goi nho|retro|vintage|classic|nostalg",
        "view_dep": r"\bview\b|toan canh|nhin ra|ho tay|canh quan|scenery",
        "san_vuon": r"san vuon|khu vuon|vuon cay|khong gian xanh|cay xanh|ngoai troi|garden|outdoor|patio",
        "rooftop": r"san thuong|tang thuong|tang mai|rooftop",
        "sang_trong": r"sang trong|cao cap|lung linh|long lay|dang cap|sang chanh|xa hoa|luxur|elegant",
        "binh_dan": r"binh dan|gia re|phai chang|sinh vien|affordable|cheap|budget",
        "tre_trung": r"tre trung|gioi tre|hien dai|nang dong|sinh vien|song ao|check-?in"
                     r"|youth|young|modern|trendy",
    },
    "features": {
        "co_live_music": r"nhac song|\blive\b|bieu dien|trinh dien|ca si|ban nhac|\bband\b|san khau|dem nhac|hat truc tiep|nghe si",
        "cho_dat_cho": r"dat cho|dat ban|reservation|\bbook",
        # \b is needed: "an nhe" also sits inside "gian nhe" (thư giãn nhẹ nhàng) and "do an"
        # also matches "do an toàn / an ninh" once accents are gone
        "do_an_nhe": r"\bdo an\b(?! toan| ninh| nhien)|\bbanh|snack|\ban nhe\b|\bmon an\b"
                     r"|thuc don do an|trai cay|hat huong duong|\bfood",
        "parking": r"gui xe|do xe|bai xe|bai do|parking|cho de xe|ham de xe",
        "khong_thuoc_la": r"khong hut thuoc|cam hut thuoc|khong khoi thuoc|non-smoking|no smoking",
        "wifi_manh": r"wi-?fi",
    },
}

# Generic "category" wording that says nothing about what the venue plays, removed before the
# tag's pattern runs. e.g. a listicle opening "Quán cafe Acoustic Hà Nội ..." must not give
# every venue under it the `acoustic` tag.
GENERIC_CATEGORY = {
    "acoustic": r"(?:ca phe|cafe|coffee|quan|phong tra|tiem)\s+acoustic(?:\s+ha noi)?",
}

MIN_NAME_LEN = 4  # shorter names/aliases are not removed from the text (too many false hits)


def _prepare(text: str, names: List[str]) -> Tuple[str, str]:
    """(haystack, haystack_without_venue_names). Both lower-cased and accent-stripped.

    The venue's own name/aliases are removed for music genres only: a place called
    "Acoustic Café" is not evidence that it plays acoustic music.
    """
    hay = fx.strip_accents(text).lower()
    music_hay = hay
    for n in names:
        n = fx.strip_accents(n or "").lower().strip()
        if len(n) >= MIN_NAME_LEN:
            music_hay = re.sub(re.escape(n), " ", music_hay)
    return hay, music_hay


def find_evidence(group: str, tag: str, hay: str, music_hay: str) -> Optional["re.Match[str]"]:
    """The regex match that justifies `tag`, or None. Tags without a rule are always kept."""
    pattern = EVIDENCE.get(group, {}).get(tag)
    if pattern is None:
        return re.search(r"", hay)  # empty match = "kept"
    base = music_hay if group == "music_genres" else hay
    if tag in GENERIC_CATEGORY:
        base = re.sub(GENERIC_CATEGORY[tag], " ", base)
    return re.search(pattern, base)


def apply_aliases(tags: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Merge tags that mean the same (e.g. co_dien -> co_kinh), keeping order, no duplicates."""
    return {k: list(dict.fromkeys(TAG_ALIASES.get(v, v) for v in vals)) for k, vals in tags.items()}


def validate_tags(
    tags: Dict[str, List[str]], text: str, names: Optional[List[str]] = None
) -> Tuple[Dict[str, List[str]], List[str]]:
    """Merge aliases, then drop tags without evidence in `text`. Returns (tags, dropped)."""
    hay, music_hay = _prepare(text, names or [])
    out = apply_aliases(tags)
    dropped: List[str] = []
    for group in EVIDENCE:
        kept = []
        for tag in out.get(group, []):
            if find_evidence(group, tag, hay, music_hay):
                kept.append(tag)
            else:
                dropped.append(f"{group}:{tag}")
        out[group] = kept
    return out, dropped


def explain(entity: Dict[str, Any], raw_tags: Optional[Dict[str, List[str]]]) -> None:
    """Print, for each tag the LLM gave, the text that justifies it (or that nothing does)."""
    names = [entity.get("name", "")] + list(entity.get("aliases") or [])
    print(f"\n=== {entity.get('name')} ===")
    if raw_tags is None:
        print("  (no cached LLM answer for this entity)")
        return
    hay, music_hay = _prepare(entity_text(entity), names)
    for group, tags in apply_aliases(raw_tags).items():
        for tag in tags:
            m = find_evidence(group, tag, hay, music_hay)
            if not m:
                print(f"  DROP {group}:{tag}  (no evidence)")
            elif m.group(0) == "":
                print(f"  KEEP {group}:{tag}  (no rule)")
            else:
                base = music_hay if group == "music_genres" else hay
                s, e = m.start(), m.end()
                print(f"  KEEP {group}:{tag}  ...{base[max(0, s - 30):e + 30]}...")


def build_prompt(items: List[Tuple[int, str, str]]) -> str:
    payload = [{"id": i, "name": name, "description": text} for i, name, text in items]
    return f"""Bạn là chuyên gia phân loại phòng trà/quán cafe tại Hà Nội.
Nhiệm vụ: với MỖI quán trong danh sách, đọc mô tả và gán các thẻ (tags) phù hợp.
Mô tả có thể gộp từ nhiều nguồn và có thể bằng tiếng Việt hoặc tiếng Anh; vẫn gán tag theo nghĩa.

DANH SÁCH TAG ĐƯỢC PHÉP DÙNG (tên tag: ý nghĩa). Chỉ dùng đúng tên tag bên trái:
{TAXONOMY_PROMPT}

QUY TẮC:
1. Chỉ gán tag khi mô tả có bằng chứng rõ ràng hoặc suy luận trực tiếp được
   (vd: "nhạc Trịnh" -> nhac_trinh; "sân khấu", "ca sĩ hát" -> co_live_music;
   "nhà cổ", "kiến trúc xưa" -> co_kinh; "phong cách cổ điển châu Âu" -> co_dien).
2. KHÔNG đoán bừa. Nếu không có căn cứ cho một mục thì để mảng rỗng [].
3. Không gán các tag mâu thuẫn nhau (vd: sang_trong và binh_dan).
4. Mỗi mục thường chỉ cần 1-4 tag, chỉ chọn tag nổi bật nhất.
5. Trả về DUY NHẤT JSON hợp lệ, không markdown, không giải thích, đúng format:
{{"results": [{{"id": 0, "music_genres": [], "atmosphere_tags": [], "features": []}}]}}
Phải có đúng một phần tử cho mỗi id đầu vào.

DANH SÁCH QUÁN:
{json.dumps(payload, ensure_ascii=False)}
"""


def call_llm(items: List[Tuple[int, str, str]]) -> Dict[int, Dict[str, List[str]]]:
    """One LLM call for a whole batch. Returns {id: cleaned_tags}."""
    data = llm_client.generate_json(build_prompt(items), temperature=0)
    rows = data.get("results", []) if isinstance(data, dict) else data

    valid_ids = {i for i, _, _ in items}
    out: Dict[int, Dict[str, List[str]]] = {}
    for row in rows:
        try:
            rid = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if rid in valid_ids:
            out[rid] = clean_tags(row)
    return out


def process_batch(
    items: List[Tuple[int, str, str]], max_retries: int = 3
) -> Dict[int, Dict[str, List[str]]]:
    """Tag one batch. Items that are missing or failed are retried one by one."""
    results: Dict[int, Dict[str, List[str]]] = {}
    pending = items

    for attempt in range(max_retries):
        try:
            got = call_llm(pending)
        except Exception as e:  # bad JSON, timeout, quota left after client retries...
            wait = (2 ** attempt) * 2 + random.random()
            logger.warning("Batch failed", size=len(pending), attempt=attempt + 1,
                           retries=max_retries, error=str(e), retry_in_s=round(wait, 1))
            time.sleep(wait)
            continue

        results.update(got)
        pending = [it for it in pending if it[0] not in got]
        if not pending:
            return results

    if len(items) > 1:
        for it in pending:
            results.update(process_batch([it], max_retries))
    else:
        logger.error("Failed to tag", name=items[0][1])
    return results


def load_cache(path: Path) -> Dict[str, Dict[str, List[str]]]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Cannot read cache", path=str(path), error=str(e))
    return {}


def save_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic: an interrupted run cannot leave a half-written file


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None,
                        help="default: llm_cleaned_entities.json, else cleaned_entities.json")
    parser.add_argument("--output", default="data/processed/tagged_entities.json")
    parser.add_argument("--cache", default="data/processed/tag_cache_v2.json")
    parser.add_argument("--workers", type=int, default=2,
                        help="requests are limited by GEMINI_RPM, so few workers are enough")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--retag", action="store_true", help="ignore the cache, tag everything again")
    parser.add_argument("--show-dropped", action="store_true",
                        help="print which tags were removed for each entity")
    parser.add_argument("--explain", metavar="NAME", default=None,
                        help="show the evidence for each tag of entities whose name contains NAME, then exit")
    args = parser.parse_args(argv)

    input_path = Path(args.input) if args.input else next(
        (Path(p) for p in ("data/processed/llm_cleaned_entities.json",
                           "data/processed/cleaned_entities.json") if Path(p).exists()),
        Path("data/processed/llm_cleaned_entities.json"),
    )
    output_path, cache_path = Path(args.output), Path(args.cache)
    if not input_path.exists():
        logger.error("Input file not found", path=str(input_path))
        return

    entities = json.loads(input_path.read_text(encoding="utf-8"))
    cache = {} if args.retag else load_cache(cache_path)

    if args.explain:
        needle = fx.strip_accents(args.explain).lower()
        for e in entities:
            if needle in fx.strip_accents(e.get("name", "")).lower():
                explain(e, cache.get(entity_key(e)))
        return

    todo: List[Tuple[int, str, str]] = []
    for i, e in enumerate(entities):
        text = entity_text(e)
        if len(text) < MIN_TEXT_LEN or entity_key(e) in cache:
            continue
        todo.append((i, e.get("name", ""), text))

    logger.info("Tagging plan", input=str(input_path), entities=len(entities),
                to_tag=len(todo), model=llm_client.MODEL_NAME)

    if todo:
        try:
            llm_client.get_client()
        except RuntimeError as exc:
            logger.error("Tagging skipped", reason=str(exc))
            todo = []

    batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_batch, b): b for b in batches}
        for fut in as_completed(futures):
            try:
                results = fut.result()
            except Exception as exc:
                logger.error("Batch crashed", error=str(exc))
                continue
            for idx, tags in results.items():
                cache[entity_key(entities[idx])] = tags
            done += len(futures[fut])
            save_json(cache_path, cache)  # checkpoint: a re-run resumes from here
            logger.info("Progress", done=done, total=len(todo))

    failed = 0
    dropped_total: Dict[str, int] = {}
    for e in entities:
        tags = cache.get(entity_key(e))
        if tags is None:
            failed += 1 if len(entity_text(e)) >= MIN_TEXT_LEN else 0
            tags = EMPTY_TAGS
        else:
            names = [e.get("name", "")] + list(e.get("aliases") or [])
            tags, dropped = validate_tags(tags, entity_text(e), names)  # cache keeps the raw LLM answer
            for d in dropped:
                dropped_total[d] = dropped_total.get(d, 0) + 1
            if args.show_dropped and dropped:
                print(f"  {e.get('name')}: removed {dropped}")
        e["music_genres"] = list(tags["music_genres"])
        e["atmosphere_tags"] = list(tags["atmosphere_tags"])
        e["features"] = list(tags["features"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(output_path, entities)

    n = len(entities) or 1
    print("\n" + "=" * 60)
    print(f"LLM TAGGING STATISTICS ({llm_client.MODEL_NAME})")
    print("=" * 60)
    print(f"Total entities: {len(entities)}")
    for label, key in (("music genres", "music_genres"), ("atmosphere tags", "atmosphere_tags"),
                       ("features", "features")):
        c = sum(1 for e in entities if e[key])
        print(f"With {label}: {c}/{n} ({c * 100 // n}%)")
    if dropped_total:
        total = sum(dropped_total.values())
        print(f"Removed {total} tags without evidence in the text: {dropped_total}")
    if failed:
        print(f"{failed} entities not tagged yet -> run again (cached results are reused)")
    print("=" * 60)
    print(f"Saved tagged data to {output_path}")


if __name__ == "__main__":
    main()