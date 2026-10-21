"""Nạp data/processed/tagged_entities.json vào SQL Server (upsert theo `id` = slug).

Chạy từ thư mục gốc repo:
    python -m src.database.load_sql --create-tables
    python -m src.database.load_sql --prune          # xóa quán không còn trong file
    python -m src.database.load_sql --recreate       # XÓA toàn bộ bảng rồi tạo lại
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from dotenv import load_dotenv
from sqlalchemy import select

from src.database.sql_client import SQLClient
from src.database.sql_models import (
    TeaRoom, TeaRoomAlias, TeaRoomDescription, TeaRoomImage, TeaRoomSource, TeaRoomTag,
)

logger = structlog.get_logger()

DEFAULT_INPUT = Path("data/processed/tagged_entities.json")
TAG_TYPES = ("music_genres", "atmosphere_tags", "features")


def _clean(value: Any) -> Any:
    """Chuỗi rỗng / toàn khoảng trắng -> None (SQL dùng NULL cho 'thiếu')."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _limit(model: type, column: str) -> int | None:
    return model.__table__.c[column].type.length


def _text(model: type, column: str, value: Any, slug: str) -> str | None:
    """Làm sạch và cắt theo độ dài cột (SQL Server báo lỗi nếu chuỗi vượt cột)."""
    value = _clean(value)
    limit = _limit(model, column)
    if isinstance(value, str) and limit and len(value) > limit:
        logger.warning("clipped_field", id=slug, column=column, length=len(value), limit=limit)
        value = value[:limit]
    return value


def _url(model: type, column: str, value: Any, slug: str) -> str | None:
    """URL quá dài thì bỏ chứ không cắt (URL bị cắt là URL hỏng)."""
    value = _clean(value)
    limit = _limit(model, column)
    if isinstance(value, str) and limit and len(value) > limit:
        logger.warning("skipped_long_url", id=slug, column=column, length=len(value))
        return None
    return value


def upsert_entity(session, entity: dict[str, Any]) -> tuple[TeaRoom, bool]:
    """Thêm mới hoặc cập nhật một quán. Bảng con được thay mới hoàn toàn."""
    slug = _clean(entity.get("id"))
    if not slug:
        raise ValueError(f"Thực thể thiếu id: {entity.get('name')!r}")

    room = session.scalar(select(TeaRoom).where(TeaRoom.slug == slug))
    created = room is None
    if created:
        room = TeaRoom(slug=slug)
        session.add(room)

    name = _text(TeaRoom, "name", entity.get("name"), slug)
    if not name:
        raise ValueError(f"Thực thể {slug!r} thiếu name")

    room.name = name
    room.address = _clean(entity.get("address"))
    room.district = _text(TeaRoom, "district", entity.get("district"), slug)
    room.price = _text(TeaRoom, "price", entity.get("price"), slug)
    room.price_min = _to_int(entity.get("price_min"))
    room.price_max = _to_int(entity.get("price_max"))
    room.hours = _text(TeaRoom, "hours", entity.get("hours"), slug)
    room.address_alternatives = entity.get("address_alternatives") or None
    room.llm_filled = entity.get("llm_filled") or None

    # Xóa con cũ và flush trước khi thêm con mới (tránh đụng ràng buộc unique)
    for rel in (room.aliases, room.descriptions, room.images, room.sources, room.tags):
        rel.clear()
    session.flush()

    for d in entity.get("descriptions") or []:
        text = _clean(d.get("text"))
        if text:
            room.descriptions.append(TeaRoomDescription(
                source_name=_text(TeaRoomDescription, "source_name", d.get("source"), slug),
                language=_text(TeaRoomDescription, "language", d.get("language"), slug),
                text=text,
            ))
    room.description = _clean(entity.get("description")) or (
        room.descriptions[0].text if room.descriptions else None
    )

    seen_alias: set[str] = set()
    for alias in entity.get("aliases") or []:
        alias = _text(TeaRoomAlias, "alias_name", alias, slug)
        if alias and alias != room.name and alias not in seen_alias:
            seen_alias.add(alias)
            room.aliases.append(TeaRoomAlias(alias_name=alias))

    urls: list[str] = []
    candidates = [entity.get("image_url"), *(entity.get("image_urls") or [])]
    for raw in candidates:
        url = _url(TeaRoomImage, "image_url", raw, slug)
        if url and url not in urls:
            urls.append(url)
    for i, url in enumerate(urls):
        room.images.append(TeaRoomImage(image_url=url, is_primary=(i == 0), order_index=i))
    room.image_url = urls[0] if urls else None

    for s in entity.get("sources") or []:
        source_name = _text(TeaRoomSource, "source_name", s.get("domain") or s.get("source_name"), slug)
        if not source_name:
            continue
        room.sources.append(TeaRoomSource(
            source_name=source_name,
            source_url=_url(TeaRoomSource, "source_url", s.get("source_url"), slug),
            raw_name=_text(TeaRoomSource, "raw_name", s.get("raw_name"), slug),
            crawled_at=_parse_dt(s.get("crawled_at")),
        ))

    for tag_type in TAG_TYPES:
        seen_tag: set[str] = set()
        for value in entity.get(tag_type) or []:
            value = _text(TeaRoomTag, "tag_value", value, slug)
            if value and value not in seen_tag:
                seen_tag.add(value)
                room.tags.append(TeaRoomTag(tag_type=tag_type, tag_value=value))

    room.source_count = _to_int(entity.get("source_count")) or len(room.sources) or 1
    return room, created


def load_entities(client: SQLClient, entities: list[dict[str, Any]], prune: bool = False) -> dict[str, int]:
    """Nạp toàn bộ trong MỘT transaction: lỗi ở bất kỳ quán nào thì không ghi gì cả."""
    if prune and not entities:
        raise ValueError("Từ chối --prune khi danh sách rỗng (sẽ xóa sạch bảng).")

    dupes = [k for k, n in Counter(e.get("id") for e in entities).items() if n > 1]
    if dupes:
        raise ValueError(f"id bị trùng trong file: {dupes}")

    stats = {"created": 0, "updated": 0, "deleted": 0}
    with client.get_session() as session:
        for entity in entities:
            _, created = upsert_entity(session, entity)
            stats["created" if created else "updated"] += 1

        if prune:
            keep = {e["id"] for e in entities}
            for room in session.scalars(select(TeaRoom).where(TeaRoom.slug.not_in(keep))).all():
                session.delete(room)
                stats["deleted"] += 1
    logger.info("load_done", **stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Nạp tagged_entities.json vào SQL Server.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--database-url", default=None, help="Mặc định lấy SQL_SERVER_URL trong .env")
    parser.add_argument("--create-tables", action="store_true", help="Tạo bảng nếu chưa có")
    parser.add_argument("--recreate", action="store_true", help="XÓA mọi bảng rồi tạo lại")
    parser.add_argument("--prune", action="store_true", help="Xóa quán không còn trong file")
    args = parser.parse_args(argv)

    path = Path(args.input)
    entities = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entities, list):
        logger.error("input_not_a_list", path=str(path))
        return 1

    client = SQLClient(args.database_url)
    if not client.test_connection():
        return 1
    if args.recreate:
        client.drop_tables()
    if args.create_tables or args.recreate:
        client.create_tables()

    stats = load_entities(client, entities, prune=args.prune)
    print(f"Xong: {len(entities)} quán trong file | {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())