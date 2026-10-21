"""SQLAlchemy models for SQL Server database."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, 
    ForeignKey, Index, create_engine
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.pool import QueuePool

Base = declarative_base()


class TeaRoom(Base):
    """Bảng chính lưu thông tin phòng trà."""
    __tablename__ = "tea_rooms"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    address = Column(Text, nullable=True)
    price = Column(String(100), nullable=True)
    hours = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    source_count = Column(Integer, default=1)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    aliases = relationship("TeaRoomAlias", back_populates="tea_room", cascade="all, delete-orphan")
    images = relationship("TeaRoomImage", back_populates="tea_room", cascade="all, delete-orphan")
    sources = relationship("TeaRoomSource", back_populates="tea_room", cascade="all, delete-orphan")
    tags = relationship("TeaRoomTag", back_populates="tea_room", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_name', 'name'),
        Index('idx_address', 'address', postgresql_using='gin'),  # Full-text search
    )
    
    def __repr__(self):
        return f"<TeaRoom(id={self.id}, name='{self.name}')>"


class TeaRoomAlias(Base):
    """Bảng lưu các tên gọi khác của phòng trà."""
    __tablename__ = "tea_room_aliases"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    alias_name = Column(String(255), nullable=False)
    
    tea_room = relationship("TeaRoom", back_populates="aliases")
    
    __table_args__ = (
        Index('idx_alias_name', 'alias_name'),
    )


class TeaRoomImage(Base):
    """Bảng lưu ảnh của phòng trà."""
    __tablename__ = "tea_room_images"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    image_url = Column(String(500), nullable=False)
    is_primary = Column(Integer, default=0)  # 1 = primary, 0 = secondary
    order_index = Column(Integer, default=0)
    
    tea_room = relationship("TeaRoom", back_populates="images")


class TeaRoomSource(Base):
    """Bảng lưu nguồn dữ liệu."""
    __tablename__ = "tea_room_sources"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    domain = Column(String(100), nullable=False)
    raw_name = Column(String(255), nullable=True)
    
    tea_room = relationship("TeaRoom", back_populates="sources")
    
    __table_args__ = (
        Index('idx_domain', 'domain'),
    )


class TeaRoomTag(Base):
    """Bảng lưu tags (music genres, atmosphere, features)."""
    __tablename__ = "tea_room_tags"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    tag_type = Column(String(50), nullable=False)  # music_genres, atmosphere_tags, features
    tag_value = Column(String(100), nullable=False)
    
    tea_room = relationship("TeaRoom", back_populates="tags")
    
    __table_args__ = (
        Index('idx_tag_type_value', 'tag_type', 'tag_value'),
    )