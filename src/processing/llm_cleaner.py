"""LLM cleaner: fills only what deterministic_cleaner could not find.

Input : data/processed/cleaned_entities.json    (entities carry `needs_llm`)
Output: data/processed/llm_cleaned_entities.json (its own file)
Cache : data/processed/llm_cache_v2.json, keyed by a hash of the text (not the row index)

The LLM never rewrites descriptions. It only returns address / price / hours quoted from the
text, and every answer is validated with the same extractors before it is accepted.
"""
import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog

from src.processing import field_extractors as fx
from src.processing import llm_client

logger = structlog.get_logger()

BATCH_SIZE = int(os.getenv("LLM_CLEAN_BATCH", "8"))
MAX_WORKERS = 2  # the limit is RPM (shared limiter), not parallelism
TEXT_LIMIT = int(os.getenv("LLM_TEXT_LIMIT", "6000"))  # safety cap per entity, not content trimming
PROMPT_VERSION = "v2"

PROMPT = """Bạn trích xuất thông tin từ văn bản về các quán (phòng trà, cafe acoustic, lounge) ở Hà Nội.
Văn bản có thể bằng tiếng Việt hoặc tiếng Anh. Dưới đây là mảng JSON; mỗi phần tử có "id", "name",
"need" (các trường cần tìm) và "text".

QUY TẮC:
- Chỉ lấy thông tin CÓ TRONG văn bản, giữ nguyên cách viết gốc. Không suy đoán, không thêm gì.
- Không tìm thấy thì để chuỗi rỗng "".
- "address": chỉ địa chỉ vật lý (số nhà, ngõ, phố/đường, quận), không kèm giờ, giá hay câu giới thiệu.
- "price": khoảng giá đồ uống/vé, ví dụ '20.000 - 55.000 VNĐ'.
- "hours": giờ mở cửa/phục vụ, ví dụ '07:00 - 23:00' hoặc 'Mở cửa 24h'. Không lấy giờ biểu diễn.
- Chỉ điền các trường có trong "need"; các trường còn lại để "".

Trả về DUY NHẤT JSON: {{"results": [{{"id": 0, "address": "", "price": "", "hours": ""}}]}}
Giữ nguyên "id". Không markdown, không giải thích.

Dữ liệu:
{payload}
"""


def entity_text(e: Dict[str, Any]) -> str:
    parts = [(t.get("text") if isinstance(t, dict) else str(t)) or "" for t in e.get("raw_texts", [])]
    return "\n---\n".join(p.strip() for p in parts if p.strip())[:TEXT_LIMIT]


def cache_key(e: Dict[str, Any], text: str) -> str:
    raw = f"{PROMPT_VERSION}|{e.get('name', '')}|{','.join(e.get('needs_llm', []))}|{text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def process_batch(batch: List[Tuple[int, Dict[str, Any], str]]) -> Dict[int, Dict[str, str]]:
    """batch: (global_index, entity, text). Returns {global_index: {address, price, hours}}."""
    payload = [
        {"id": i, "name": e["name"], "need": e["needs_llm"], "text": text}
        for i, (_, e, text) in enumerate(batch)
    ]
    prompt = PROMPT.format(payload=json.dumps(payload, ensure_ascii=False))
    results: Dict[int, Dict[str, str]] = {}
    try:
        data = llm_client.generate_json(prompt, temperature=0)
        rows = data.get("results", []) if isinstance(data, dict) else data
        for row in rows:
            local = row.get("id")
            if isinstance(local, int) and 0 <= local < len(batch):
                results[batch[local][0]] = {
                    k: str(row.get(k, "") or "").strip() for k in ("address", "price", "hours")
                }
    except Exception as exc:
        logger.error("Batch failed", error=str(exc))
    return results


def apply_result(e: Dict[str, Any], got: Dict[str, str]) -> List[str]:
    """Validate, then fill only the fields that are still empty. Returns the fields filled."""
    filled: List[str] = []
    if not e.get("address") and fx.is_valid_address(got.get("address", "")):
        e["address"] = got["address"].strip()
        e["district"] = fx.find_district(e["address"])
        filled.append("address")
    if not e.get("price"):
        parsed = fx.parse_price(got.get("price", ""))
        if parsed:
            e["price"] = fx.format_price(*parsed)
            e["price_min"], e["price_max"] = parsed
            filled.append("price")
    if not e.get("hours"):
        hours = fx.normalize_hours(got.get("hours", ""))
        if hours:
            e["hours"] = hours
            filled.append("hours")
    return filled


def load_cache(path: Path) -> Dict[str, Dict[str, str]]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Unreadable cache, starting empty", path=str(path))
    return {}


def save_json(path: Path, obj: Any, indent: Optional[int] = None) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    tmp.replace(path)


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="data/processed/cleaned_entities.json")
    p.add_argument("--output", default="data/processed/llm_cleaned_entities.json")
    p.add_argument("--cache", default="data/processed/llm_cache_v2.json")
    args = p.parse_args(argv)

    in_path, out_path, cache_path = Path(args.input), Path(args.output), Path(args.cache)
    if not in_path.exists():
        logger.error("Input file not found", path=str(in_path))
        return

    entities = json.loads(in_path.read_text(encoding="utf-8"))
    cache = load_cache(cache_path)

    todo: List[Tuple[int, Dict[str, Any], str]] = []
    for i, e in enumerate(entities):
        if not e.get("needs_llm"):
            continue
        text = entity_text(e)
        if len(text) < 30 or cache_key(e, text) in cache:
            continue
        todo.append((i, e, text))

    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    n_need = sum(1 for e in entities if e.get("needs_llm"))
    logger.info("LLM cleaning plan", entities=len(entities), need=n_need,
                to_request=len(todo), requests=len(batches), model=llm_client.MODEL_NAME)

    if todo:
        try:
            llm_client.get_client()
        except RuntimeError as exc:
            logger.error("LLM step skipped", reason=str(exc))
            batches = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_batch, b): b for b in batches}
        for fut in as_completed(futures):
            got = fut.result()
            for idx, e, text in futures[fut]:
                if idx in got:
                    cache[cache_key(e, text)] = got[idx]
            save_json(cache_path, cache)  # checkpoint: a re-run only asks for what is missing

    filled_total = {"address": 0, "price": 0, "hours": 0}
    pending = 0
    for e in entities:
        needs = e.get("needs_llm") or []
        if needs:
            got = cache.get(cache_key(e, entity_text(e)))
            if got:
                filled = apply_result(e, got)
                for f in filled:
                    filled_total[f] += 1
                if filled:
                    e["llm_filled"] = filled
            elif len(entity_text(e)) >= 30:
                pending += 1
        e.pop("needs_llm", None)
        e.pop("raw_texts", None)  # large and no longer needed after extraction

    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(out_path, entities, indent=2)

    n = len(entities) or 1
    print("\n" + "=" * 60)
    print(f"LLM CLEANING STATISTICS ({llm_client.MODEL_NAME})")
    print("=" * 60)
    print(f"Total entities: {len(entities)}  |  asked LLM about: {n_need}")
    print(f"Filled by LLM: {filled_total}")
    for field in ("address", "price", "hours"):
        c = sum(1 for e in entities if e.get(field))
        print(f"With {field}: {c}/{len(entities)} ({c * 100 // n}%)")
    if pending:
        print(f"Not answered yet (run again, cached results are reused): {pending}")
    print("=" * 60)


if __name__ == "__main__":
    main()
