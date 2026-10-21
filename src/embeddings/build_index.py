"""Dựng index Qdrant từ SQL Server: mỗi dòng tea_room_descriptions = một điểm.

Chạy từ thư mục gốc repo:
    python -m src.embeddings.build_index --dry-run   # xem văn bản + số token, không ghi gì
    python -m src.embeddings.build_index             # embed rồi XÓA DỰNG LẠI collection
    python -m src.embeddings.build_index --no-tags   # không đưa tag vào văn bản embed

Phải chạy lại sau mỗi lần chạy `load_sql` (id của bảng mô tả đổi mỗi lần nạp).
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import structlog
from dotenv import load_dotenv
from qdrant_client.models import PointStruct
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.database.client import get_qdrant_client, settings
from src.database.indexer import create_collection, create_payload_indexes
from src.database.schema import PhongTraPayload
from src.database.sql_client import SQLClient
from src.database.sql_models import TeaRoom
from src.embeddings.embedder import Embedder, build_text

logger = structlog.get_logger()


def load_items(sql: SQLClient, include_tags: bool = True) -> list[dict[str, Any]]:
    """Đọc SQL, trả list {id, text, payload} (mỗi mô tả một phần tử)."""
    items: list[dict[str, Any]] = []
    with sql.get_session() as session:
        rooms = session.scalars(
            select(TeaRoom)
            .options(
                selectinload(TeaRoom.tags),
                selectinload(TeaRoom.descriptions),
                selectinload(TeaRoom.sources),
            )
            .order_by(TeaRoom.id)
        ).all()

        for room in rooms:
            if not room.descriptions:
                logger.warning("room_without_description", slug=room.slug)
                continue

            tags: dict[str, list[str]] = {"music_genres": [], "atmosphere_tags": [], "features": []}
            for tag in room.tags:
                tags.setdefault(tag.tag_type, []).append(tag.tag_value)
            for values in tags.values():
                values.sort()
            source_urls = {s.source_name: s.source_url for s in room.sources}

            for d in room.descriptions:
                payload = PhongTraPayload(
                    tea_room_id=room.id,
                    description_id=d.id,
                    slug=room.slug,
                    name=room.name,
                    address=room.address,
                    district=room.district,
                    music_genres=tags["music_genres"],
                    atmosphere_tags=tags["atmosphere_tags"],
                    features=tags["features"],
                    price_min_vnd=room.price_min,
                    price_max_vnd=room.price_max,
                    hours=room.hours,
                    image_url=room.image_url,
                    source_name=d.source_name,
                    source_url=source_urls.get(d.source_name),
                    text=d.text,
                ).model_dump()
                items.append({
                    "id": d.id,
                    "text": build_text(room.name, room.district, tags, d.text, include_tags),
                    "payload": payload,
                })
    return items


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Embed mô tả và ghi vào Qdrant.")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ xem văn bản và số token")
    parser.add_argument("--no-tags", action="store_true", help="Không đưa tag vào văn bản embed")
    args = parser.parse_args(argv)

    items = load_items(SQLClient(), include_tags=not args.no_tags)
    if not items:
        logger.error("no_items_found")
        return 1

    texts = [i["text"] for i in items]
    rooms = len({i["payload"]["tea_room_id"] for i in items})
    embedder = Embedder()
    stats = embedder.token_stats(texts)
    print(f"{len(items)} điểm từ {rooms} quán | token: {stats}")
    if stats["max"] > stats["limit"]:
        print(f"CẢNH BÁO: có văn bản dài hơn giới hạn {stats['limit']} token, phần cuối sẽ bị cắt.")

    if args.dry_run:
        for item in items[:2]:
            print("---")
            print(item["text"][:400])
        return 0

    qdrant = get_qdrant_client()
    try:
        qdrant.get_collections()
    except Exception as exc:  # noqa: BLE001
        logger.error("qdrant_unreachable", url=settings.qdrant_url, error=str(exc))
        return 1

    vectors = embedder.encode(texts)  # embed xong mới xóa collection cũ
    create_collection(qdrant, recreate=True)
    create_payload_indexes(qdrant)

    points = [
        PointStruct(id=i["id"], vector=v, payload=i["payload"])
        for i, v in zip(items, vectors)
    ]
    batch = settings.qdrant_upsert_batch
    for start in range(0, len(points), batch):
        qdrant.upsert(
            collection_name=settings.qdrant_collection,
            points=points[start:start + batch],
            wait=True,
        )

    count = qdrant.count(collection_name=settings.qdrant_collection, exact=True).count
    print(f"Xong: {count} điểm trong collection '{settings.qdrant_collection}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())