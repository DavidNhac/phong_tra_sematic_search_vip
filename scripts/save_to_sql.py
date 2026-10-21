"""Script to save cleaned and tagged entities to SQL Server."""
import json
from pathlib import Path
from typing import List, Dict, Any
import structlog

from src.database.sql_client import get_sql_client
from src.database.sql_models import (
    TeaRoom, TeaRoomAlias, TeaRoomImage, TeaRoomSource, TeaRoomTag
)

logger = structlog.get_logger()


def save_entity_to_sql(session, entity: Dict[str, Any]) -> TeaRoom:
    """Lưu 1 entity vào SQL Server với đầy đủ relationships."""
    
    # 1. Tạo TeaRoom chính
    tea_room = TeaRoom(
        name=entity.get("name", ""),
        address=entity.get("address", ""),
        price=entity.get("price", ""),
        hours=entity.get("hours", ""),
        description=entity.get("description", ""),
        image_url=entity.get("image_url", ""),
        source_count=entity.get("source_count", 1),
    )
    session.add(tea_room)
    session.flush()  # Để lấy ID
    
    # 2. Lưu aliases
    for alias_name in entity.get("aliases", []):
        alias = TeaRoomAlias(
            tea_room_id=tea_room.id,
            alias_name=alias_name
        )
        session.add(alias)
    
    # 3. Lưu images
    image_urls = entity.get("image_urls", [])
    for idx, img_url in enumerate(image_urls):
        image = TeaRoomImage(
            tea_room_id=tea_room.id,
            image_url=img_url,
            is_primary=1 if idx == 0 else 0,
            order_index=idx
        )
        session.add(image)
    
    # 4. Lưu sources
    for source in entity.get("sources", []):
        source_obj = TeaRoomSource(
            tea_room_id=tea_room.id,
            domain=source.get("domain", ""),
            raw_name=source.get("raw_name", "")
        )
        session.add(source_obj)
    
    # 5. Lưu tags
    tag_mappings = [
        ("music_genres", entity.get("music_genres", [])),
        ("atmosphere_tags", entity.get("atmosphere_tags", [])),
        ("features", entity.get("features", [])),
    ]
    
    for tag_type, tag_values in tag_mappings:
        for tag_value in tag_values:
            tag = TeaRoomTag(
                tea_room_id=tea_room.id,
                tag_type=tag_type,
                tag_value=tag_value
            )
            session.add(tag)
    
    return tea_room


def main():
    input_path = Path("data/processed/tagged_entities.json")
    
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        logger.info("Please run llm_tagger.py first")
        return
    
    with open(input_path, "r", encoding="utf-8") as f:
        entities = json.load(f)
    
    logger.info(f"Loaded {len(entities)} entities from {input_path}")
    
    # Khởi tạo SQL client
    sql_client = get_sql_client()
    
    # Test connection
    if not sql_client.test_connection():
        logger.error("Cannot connect to SQL Server. Please check SQL_SERVER_URL in .env")
        return
    
    # Tạo tables
    sql_client.create_tables()
    
    # Xóa dữ liệu cũ (optional - bỏ comment nếu muốn clean slate)
    # sql_client.drop_tables()
    # sql_client.create_tables()
    
    # Lưu từng entity
    logger.info("Starting to save entities to SQL Server...")
    saved_count = 0
    
    with sql_client.get_session() as session:
        for i, entity in enumerate(entities):
            try:
                tea_room = save_entity_to_sql(session, entity)
                saved_count += 1
                
                if (i + 1) % 10 == 0:
                    logger.info(f"Progress: {i + 1}/{len(entities)}")
                    
            except Exception as e:
                logger.error(f"Failed to save entity '{entity.get('name')}': {e}")
    
    logger.info(f"✅ Successfully saved {saved_count}/{len(entities)} entities to SQL Server")
    
    # Thống kê
    with sql_client.get_session() as session:
        from sqlalchemy import func
        
        total_rooms = session.query(func.count(TeaRoom.id)).scalar()
        total_aliases = session.query(func.count(TeaRoomAlias.id)).scalar()
        total_images = session.query(func.count(TeaRoomImage.id)).scalar()
        total_sources = session.query(func.count(TeaRoomSource.id)).scalar()
        total_tags = session.query(func.count(TeaRoomTag.id)).scalar()
        
        print("\n" + "=" * 60)
        print("SQL SERVER STATISTICS")
        print("=" * 60)
        print(f"Tea Rooms: {total_rooms}")
        print(f"Aliases: {total_aliases}")
        print(f"Images: {total_images}")
        print(f"Sources: {total_sources}")
        print(f"Tags: {total_tags}")
        print("=" * 60)


if __name__ == "__main__":
    main()