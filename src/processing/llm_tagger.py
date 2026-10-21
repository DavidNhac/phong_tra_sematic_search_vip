"""Use an LLM to generate structured tags for each entity (batched, parallel, resumable).

Uses the shared llm_client (google-genai, global rate limiter, 429 retry). Tags are generated
from ALL source descriptions of an entity, not just one.
"""


import argparse
import hashlib
import json
import random
import unicodedata
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

def _fold(text: str) -> str:
    """Lowercase + strip accents (đ -> d)."""
    s = unicodedata.normalize("NFC", text).lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
# A tag is kept only if the description contains evidence for it (accent-insensitive).
# Small LLMs tend to add plausible-sounding tags (parking, snacks) that no source mentions.
# for tags whose stripped form is ambiguous ("trinh" also matches "chương trình", "trình diễn").
EVIDENCE = {
    "music_genres": {
        "acoustic": r"nhac acoustic|acoustic live|acoustic (band|show|cover|music|session)|"
                    r"dem acoustic|(bieu dien|trinh dien|choi|hat) acoustic|"
                    r"acoustic (cafe|coffee)|guitar",
        "jazz": r"jazz",
        "bolero": r"bolero|nhac vang|tien chien",
        "lofi_chill": r"lofi|lo-fi|nhac chill|chill ?out",
        "indie": r"indie|underground",
        "traditional": r"dan gian|co truyen|dan toc|truyen thong|dan ca|ca tru|cheo|quan ho",
        "piano": r"piano",
        "pop_ballad": r"\bpop\b|ballad|\bnhac tre\b|nhac tinh cam",
    },
    "features": {
        "co_live_music": r"nhac song|\blive\b|bieu dien|trinh dien(?! ra)|ca si|ban nhac|\bband\b|"
                         r"san khau|dem nhac|hat truc tiep|nghe si",
        "cho_dat_cho": r"dat cho|dat ban|reservation|\bbook",
        "parking": r"gui xe|do xe|bai xe|bai do|parking|cho de xe|ham de xe",
        "khong_thuoc_la": r"khong hut thuoc|cam hut thuoc|khong khoi thuoc|non-smoking|no smoking",
        "wifi_manh": r"wi-?fi",
    },
}
EVIDENCE_ACCENTED = {
    "music_genres": {
        "nhac_trinh": r"nhạc\s+trịnh|trịnh\s+công\s+sơn|ca\s+khúc\s+trịnh|nhạc\s+sĩ\s+trịnh",
    },
    "features": {
        "do_an_nhe": r"đồ\s+ăn|món\s+ăn|ăn\s+nhẹ|ăn\s+vặt|\bbánh|snack|trái\s+cây|"
                     r"hạt\s+hướng\s+dương|\bfood",
    },
}



def validate_tags(tags: Dict[str, List[str]], text: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """Drop music_genres/features without evidence in `text`. Returns (tags, dropped)."""
    folded = _fold(text)
    accented = unicodedata.normalize("NFC", text).lower()
    out = {k: list(v) for k, v in tags.items()}
    dropped: List[str] = []
    for group in ("music_genres", "features"):
        plain, acc = EVIDENCE.get(group, {}), EVIDENCE_ACCENTED.get(group, {})
        kept = []
        for tag in out.get(group, []):
            if tag in acc:
                ok = re.search(acc[tag], accented)
            else:
                ok = tag in plain and re.search(plain[tag], folded)
            if ok:
                kept.append(tag)
            else:
                dropped.append(f"{group}:{tag}")
        out[group] = kept
    return out, dropped


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
            tags, dropped = validate_tags(tags, entity_text(e))  # cache keeps the raw LLM answer
            for d in dropped:
                dropped_total[d] = dropped_total.get(d, 0) + 1
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