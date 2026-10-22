"""Đối chiếu quán với Google Places (Places API (New), Text Search).

Điều khoản Google: chỉ place_id được lưu lâu dài; tọa độ chỉ cache tối đa 30 ngày; các trường
khác không được lưu. Vì vậy script này:
  * GHI place_id vào data/processed/google_place_ids.json (được phép lưu);
  * chỉ IN tên/địa chỉ/giờ/trạng thái của Google ra báo cáo CSV cục bộ để bạn đối chiếu bằng
    mắt rồi sửa dữ liệu của mình. Không nạp các trường đó vào SQL hay Qdrant.

Chạy từ thư mục gốc repo:
    python -m src.enrichment.google_places
    python -m src.enrichment.google_places --limit 3      # thử 3 quán trước

Cần GOOGLE_PLACES_API_KEY trong .env (không đưa vào code, không gửi cho ai).
"""
import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import structlog
from dotenv import load_dotenv
from rapidfuzz import fuzz

from src.processing import field_extractors as fx

logger = structlog.get_logger()

URL = "https://places.googleapis.com/v1/places:searchText"
# regularOpeningHours thuộc SKU cao hơn (Enterprise, free ~1.000 lượt/tháng). Với vài chục quán
# thì không đáng kể, nhưng hãy đặt cảnh báo ngân sách trong Google Cloud trước khi chạy.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.businessStatus",
    "places.regularOpeningHours.weekdayDescriptions",
])
REPORT_COLUMNS = [
    "slug", "our_name", "google_name", "name_score", "business_status",
    "our_address", "google_address", "our_hours", "google_hours", "place_id",
]
MIN_NAME_SCORE = 60  # dưới ngưỡng này coi là không khớp quán, không lưu place_id


def build_query(entity: Dict[str, Any]) -> str:
    return f"{entity['name']} {entity.get('address') or 'Hà Nội'}".strip()


def name_score(a: str, b: str) -> float:
    """So tên sau khi bỏ dấu, chữ thường."""
    return fuzz.token_set_ratio(fx.strip_accents(a).lower(), fx.strip_accents(b).lower())


def pick_place(entity_name: str, places: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Kết quả có tên giống nhất, hoặc None nếu không kết quả nào đạt ngưỡng."""
    best, best_score = None, -1.0
    for p in places:
        google_name = (p.get("displayName") or {}).get("text", "")
        score = name_score(entity_name, google_name)
        if score > best_score:
            best, best_score = p, score
    if best is None or best_score < MIN_NAME_SCORE:
        return None
    best["_score"] = best_score
    return best


def search(client: httpx.Client, api_key: str, query: str) -> List[Dict[str, Any]]:
    resp = client.post(
        URL,
        headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK},
        json={"textQuery": query, "languageCode": "vi", "regionCode": "VN", "pageSize": 3},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("places", [])


def save_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main(argv: Optional[List[str]] = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/processed/tagged_entities.json")
    parser.add_argument("--ids-output", default="data/processed/google_place_ids.json")
    parser.add_argument("--report", default="data/processed/google_check_report.csv")
    parser.add_argument("--limit", type=int, default=0, help="chỉ xử lý N quán đầu (0 = tất cả)")
    parser.add_argument("--delay", type=float, default=0.3)
    args = parser.parse_args(argv)

    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        logger.error("Thiếu GOOGLE_PLACES_API_KEY trong .env")
        return

    entities = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.limit:
        entities = entities[: args.limit]

    ids_path = Path(args.ids_output)
    place_ids: Dict[str, Dict[str, str]] = (
        json.loads(ids_path.read_text(encoding="utf-8")) if ids_path.exists() else {}
    )
    rows: List[Dict[str, Any]] = []
    unmatched: List[str] = []

    with httpx.Client() as client:
        for e in entities:
            slug = e["id"]
            try:
                places = search(client, api_key, build_query(e))
            except httpx.HTTPError as exc:
                logger.error("Google request failed", name=e["name"], error=str(exc))
                continue
            match = pick_place(e["name"], places)
            if match is None:
                unmatched.append(e["name"])
                logger.warning("No confident match", name=e["name"], results=len(places))
                time.sleep(args.delay)
                continue

            place_ids[slug] = {"place_id": match["id"]}  # chỉ place_id được lưu
            hours = (match.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []
            rows.append({
                "slug": slug,
                "our_name": e["name"],
                "google_name": (match.get("displayName") or {}).get("text", ""),
                "name_score": round(match["_score"]),
                "business_status": match.get("businessStatus", ""),
                "our_address": e.get("address", ""),
                "google_address": match.get("formattedAddress", ""),
                "our_hours": e.get("hours", ""),
                "google_hours": " | ".join(hours),
                "place_id": match["id"],
            })
            time.sleep(args.delay)

    ids_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(ids_path, place_ids)
    # utf-8-sig để Excel mở tiếng Việt không bị lỗi font
    with open(args.report, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    closed = [r["our_name"] for r in rows if r["business_status"] not in ("", "OPERATIONAL")]
    print("\n" + "=" * 60)
    print("GOOGLE PLACES CHECK")
    print("=" * 60)
    print(f"Matched: {len(rows)}/{len(entities)} | place_id saved: {len(place_ids)}")
    print(f"Not matched ({len(unmatched)}): {unmatched}")
    print(f"Not OPERATIONAL ({len(closed)}): {closed}")
    print(f"Report (local only, do not commit): {args.report}")
    print("=" * 60)


if __name__ == "__main__":
    main()