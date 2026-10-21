"""Deterministic cleaner: gap-fill from every source's raw text, then normalize formats.

Input : data/processed/resolved_entities.json
Output: data/processed/cleaned_entities.json   (its own file; nothing else writes here)

Never overwrites a valid value with an empty one. Entities that still miss address/price/hours
get `needs_llm`, so llm_cleaner only spends requests where this step could not decide.
"""

# ở đây lấy thông tin theo mẫu cố định theo các trường
import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from src.processing import field_extractors as fx

logger = structlog.get_logger()

MIN_RAW_LEN = 30
FIELDS = ("address", "price", "hours")


def _raw_texts(e: Dict[str, Any]) -> List[str]:
    items = e.get("raw_texts")
    if isinstance(items, list):
        return [(t.get("text") if isinstance(t, dict) else str(t)) or "" for t in items]
    return [e["raw_text"]] if e.get("raw_text") else []


def clean_entity(e: Dict[str, Any]) -> Dict[str, Any]:
    e["name"] = fx.clean_name(e.get("name", ""))
    e["aliases"] = sorted(
        {fx.clean_name(a) for a in e.get("aliases", []) if fx.clean_name(a)} - {e["name"]}
    )
    raws = _raw_texts(e)

    # --- address: keep a valid existing value, otherwise try every source text in order
    address = e.get("address") or ""
    if not fx.is_valid_address(address):
        address = next((a for a in (fx.extract_address(t) for t in raws) if a), "")
    e["address"] = address
    e["district"] = fx.find_district(address)

    # --- price: normalize to "20.000 - 55.000 VNĐ" and keep numeric bounds for SQL
    parsed = fx.parse_price(e.get("price") or "")
    if not parsed:
        parsed = next((p for p in (fx.parse_price(t) for t in raws) if p), None)
    e["price"] = fx.format_price(*parsed) if parsed else ""
    e["price_min"], e["price_max"] = parsed if parsed else (None, None)

    # --- hours: one canonical format regardless of who produced it
    hours = fx.normalize_hours(e.get("hours") or "")
    if not hours:
        hours = next((h for h in (fx.extract_hours(t) for t in raws) if h), "")
    e["hours"] = hours

    # --- description is never truncated here (truncate only when building embeddings)
    if not e.get("description") and e.get("descriptions"):
        e["description"] = e["descriptions"][0]["text"]

    has_text = sum(len(t.strip()) for t in raws) >= MIN_RAW_LEN
    e["needs_llm"] = [f for f in FIELDS if not e.get(f)] if has_text else []
    return e


def make_ids(entities: List[Dict[str, Any]]) -> None:
    seen: Dict[str, int] = {}
    for e in entities:
        base = fx.slugify(e["name"]) or "venue"
        if e.get("district"):
            base = f"{base}-{fx.slugify(e['district'])}" if base in seen else base
        n = seen.get(base, 0)
        seen[base] = n + 1
        e["id"] = base if n == 0 else f"{base}-{n + 1}"


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="data/processed/resolved_entities.json")
    p.add_argument("--output", default="data/processed/cleaned_entities.json")
    args = p.parse_args(argv)

    in_path, out_path = Path(args.input), Path(args.output)
    if not in_path.exists():
        logger.error("Input file not found", path=str(in_path))
        return

    entities = json.loads(in_path.read_text(encoding="utf-8"))
    cleaned = [clean_entity(e) for e in entities]
    make_ids(cleaned)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(cleaned) or 1
    print("\n" + "=" * 60)
    print("DETERMINISTIC CLEANING STATISTICS")
    print("=" * 60)
    print(f"Total entities: {len(cleaned)}")
    for field in (*FIELDS, "district", "description"):
        c = sum(1 for e in cleaned if e.get(field))
        print(f"With {field}: {c}/{len(cleaned)} ({c * 100 // n}%)")
    todo = sum(1 for e in cleaned if e["needs_llm"])
    print(f"Left for llm_cleaner: {todo} entities")
    print("=" * 60)


if __name__ == "__main__":
    main()
