"""Qdrant collection management and indexing."""
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
    PayloadSchemaType,
)
import structlog

from src.database.client import get_qdrant_client, settings

logger = structlog.get_logger()


def create_collection(client: QdrantClient) -> None:
    """Create Qdrant collection with multi-aspect vectors."""
    collection_name = settings.qdrant_collection
    
    # Check if collection exists
    collections = [c.name for c in client.get_collections().collections]
    if collection_name in collections:
        logger.warning(f"Collection '{collection_name}' already exists")
        return
    
    logger.info(f"Creating collection '{collection_name}'")
    
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "overview": VectorParams(size=1024, distance=Distance.COSINE),
            "atmosphere": VectorParams(size=1024, distance=Distance.COSINE),
            "experience": VectorParams(size=1024, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "bm25": SparseVectorParams(
                index=SparseIndexParams(on_disk=False)
            )
        }
    )
    
    logger.info(f"✅ Collection '{collection_name}' created")


def create_payload_indexes(client: QdrantClient) -> None:
    """Create payload indexes for fast filtering."""
    collection_name = settings.qdrant_collection
    
    logger.info("Creating payload indexes")
    
    # Keyword indexes
    keyword_fields = ["district", "music_genres", "atmosphere_tags", "features"]
    for field in keyword_fields:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD
        )
        logger.debug(f"Created keyword index for '{field}'")
    
    # Numeric indexes
    numeric_fields = ["price_min_vnd", "price_max_vnd", "rating"]
    for field in numeric_fields:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=PayloadSchemaType.FLOAT if field == "rating" else PayloadSchemaType.INTEGER
        )
        logger.debug(f"Created numeric index for '{field}'")
    
    logger.info("✅ Payload indexes created")


def setup_collection() -> None:
    """Setup collection with indexes."""
    client = get_qdrant_client()
    create_collection(client)
    create_payload_indexes(client)


if __name__ == "__main__":
    setup_collection()