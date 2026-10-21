"""Qdrant client configuration and connection."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from qdrant_client import QdrantClient


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # extra="ignore": .env còn chứa biến của phần khác (SQL_SERVER_URL, GEMINI_API_KEY...)
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "phong_tra_ha_noi"
    qdrant_upsert_batch: int = 100

    # Embedding
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_env: str = "development"
    log_level: str = "INFO"


# Global settings instance
settings = Settings()


def get_qdrant_client() -> QdrantClient:
    """Create and return Qdrant client instance."""
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key if settings.qdrant_api_key else None,
        timeout=30,
    )


def test_connection() -> bool:
    """Test connection to Qdrant."""
    try:
        client = get_qdrant_client()
        collections = client.get_collections()
        print(f"✅ Connected to Qdrant at {settings.qdrant_url}")
        print(f"📦 Existing collections: {[c.name for c in collections.collections]}")
        return True
    except Exception as e:
        print(f"❌ Failed to connect to Qdrant: {e}")
        return False


if __name__ == "__main__":
    test_connection()