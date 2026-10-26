"""SQL Server client and session management."""
import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
import structlog

from src.database.sql_models import Base

logger = structlog.get_logger()


class SQLClient:
    """Client để quản lý kết nối SQL Server."""
    
    def __init__(self, database_url: str = None):
        self.database_url = database_url or os.getenv("SQL_SERVER_URL")
        if not self.database_url:
            raise ValueError("SQL_SERVER_URL not found in environment variables")
        
        # Tạo engine với connection pool
        self.engine = create_engine(
            self.database_url,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=1800,  # Recycle connections every 30 minutes
            echo=False,  # Set True để debug SQL queries
        )
        
        # Tạo session factory
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        logger.info(f"[OK] SQL Client initialized: {self.database_url.split('@')[-1] if '@' in self.database_url else self.database_url}")
    
    def create_tables(self):
        """Tạo tất cả tables nếu chưa tồn tại."""
        logger.info("Creating tables if not exists...")
        Base.metadata.create_all(bind=self.engine)
        logger.info("[OK] Tables created/verified")
    
    def drop_tables(self):
        """Xóa tất cả tables (CẨN THẬN!)."""
        logger.warning("Dropping all tables...")
        Base.metadata.drop_all(bind=self.engine)
        logger.info("[OK] Tables dropped")
    
    @contextmanager
    def get_session(self) -> Session:
        """Context manager để lấy session, tự động commit/rollback."""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            session.close()
    
    def execute_raw_sql(self, sql: str, params: dict = None):
        """Thực thi raw SQL query."""
        with self.get_session() as session:
            result = session.execute(text(sql), params or {})
            return result.fetchall()
    
    def test_connection(self) -> bool:
        """Test kết nối SQL Server."""
        try:
            with self.get_session() as session:
                result = session.execute(text("SELECT 1"))
                logger.info("[OK] SQL Server connection successful")
                return True
        except Exception as e:
            logger.error(f" SQL Server connection failed: {e}")
            return False


# Singleton instance
_client_instance = None

def get_sql_client() -> SQLClient:
    """Get or create SQLClient singleton."""
    global _client_instance
    if _client_instance is None:
        _client_instance = SQLClient()
    return _client_instance