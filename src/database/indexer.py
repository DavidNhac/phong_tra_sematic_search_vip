"""Quản lý collection Qdrant: mỗi mô tả của một quán = một điểm, 2 vector có tên:
`dense` (bge-m3, cosine) và `bm25` (sparse, Qdrant tính IDF)."""
import argparse

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Modifier,
    PayloadSchemaType,
    SparseVectorParams,
    VectorParams,
)

from src.database.client import get_qdrant_client, settings

logger = structlog.get_logger()

EMBEDDING_DIM = 1024  # BAAI/bge-m3
DENSE_VECTOR = "dense"  # tên vector dense
SPARSE_VECTOR = "bm25"  # tên vector sparse (từ khóa)

KEYWORD_FIELDS = ["slug", "district", "music_genres", "atmosphere_tags", "features", "source_name"]
INTEGER_FIELDS = ["tea_room_id", "price_min_vnd", "price_max_vnd"]


def create_collection(client: QdrantClient, recreate: bool = False) -> None:
    """Tạo collection. recreate=True thì xóa collection cũ rồi tạo lại."""
    collection_name = settings.qdrant_collection

    collections = [c.name for c in client.get_collections().collections]
    if collection_name in collections:
        if not recreate:
            logger.warning(f"Collection '{collection_name}' already exists")
            return
        logger.warning(f"Deleting collection '{collection_name}' to recreate")
        client.delete_collection(collection_name)

    logger.info(f"Creating collection '{collection_name}'")
    client.create_collection(
        collection_name=collection_name,
        vectors_config={DENSE_VECTOR: VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE)},
        sparse_vectors_config={SPARSE_VECTOR: SparseVectorParams(modifier=Modifier.IDF)},
    )
    logger.info(f"✅ Collection '{collection_name}' created")


def create_payload_indexes(client: QdrantClient) -> None:
    """Tạo payload index để lọc nhanh."""
    collection_name = settings.qdrant_collection
    logger.info("Creating payload indexes")

    for field in KEYWORD_FIELDS:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
        )
    for field in INTEGER_FIELDS:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=PayloadSchemaType.INTEGER,
        )
    logger.info("✅ Payload indexes created")


def setup_collection(recreate: bool = False) -> None:
    """Setup collection with indexes."""
    client = get_qdrant_client()
    create_collection(client, recreate=recreate)
    create_payload_indexes(client)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tạo collection Qdrant rỗng + index.")
    parser.add_argument("--recreate", action="store_true", help="Xóa collection cũ rồi tạo lại")
    setup_collection(recreate=parser.parse_args().recreate)