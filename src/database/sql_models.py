"""SQLAlchemy models cho SQL Server, khớp data/processed/tagged_entities.json."""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer,
    Unicode, UnicodeText, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _utcnow() -> datetime:
    """UTC không kèm tzinfo (datetime.utcnow() bị deprecated từ Python 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TeaRoom(Base):
    """Bảng chính: một quán = một dòng. `slug` là `id` trong JSON (khóa upsert)."""
    __tablename__ = "tea_rooms"

    id = Column(Integer, primary_key=True, autoincrement=True)  # dùng làm point id cho Qdrant
    slug = Column(Unicode(120), nullable=False, unique=True)
    name = Column(Unicode(255), nullable=False, index=True)
    address = Column(UnicodeText, nullable=True)
    district = Column(Unicode(100), nullable=True, index=True)
    price = Column(Unicode(100), nullable=True)      # chuỗi gốc, vd "20.000 - 55.000 VNĐ"
    price_min = Column(Integer, nullable=True)       # VNĐ, NULL nếu không rõ
    price_max = Column(Integer, nullable=True)
    hours = Column(Unicode(100), nullable=True)
    description = Column(UnicodeText, nullable=True)  # bản mô tả chính; đủ các nguồn ở bảng descriptions
    image_url = Column(Unicode(1000), nullable=True)
    source_count = Column(Integer, nullable=False, default=1)
    address_alternatives = Column(JSON(none_as_null=True), nullable=True)
    llm_filled = Column(JSON(none_as_null=True), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    aliases = relationship("TeaRoomAlias", back_populates="tea_room", cascade="all, delete-orphan")
    descriptions = relationship("TeaRoomDescription", back_populates="tea_room", cascade="all, delete-orphan")
    images = relationship(
        "TeaRoomImage", back_populates="tea_room", cascade="all, delete-orphan",
        order_by="TeaRoomImage.order_index",
    )
    sources = relationship("TeaRoomSource", back_populates="tea_room", cascade="all, delete-orphan")
    tags = relationship("TeaRoomTag", back_populates="tea_room", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<TeaRoom(id={self.id}, slug='{self.slug}', name='{self.name}')>"


class TeaRoomAlias(Base):
    """Các tên gọi khác của quán."""
    __tablename__ = "tea_room_aliases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    alias_name = Column(Unicode(255), nullable=False)

    tea_room = relationship("TeaRoom", back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("tea_room_id", "alias_name", name="uq_alias_per_room"),
        Index("idx_alias_name", "alias_name"),
    )


class TeaRoomDescription(Base):
    """Mô tả theo từng nguồn (JSON: descriptions[]). Dùng dựng văn bản embedding."""
    __tablename__ = "tea_room_descriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    source_name = Column(Unicode(100), nullable=True)
    language = Column(Unicode(10), nullable=True)
    text = Column(UnicodeText, nullable=False)

    tea_room = relationship("TeaRoom", back_populates="descriptions")


class TeaRoomImage(Base):
    """Ảnh của quán. order_index = 0 là ảnh chính."""
    __tablename__ = "tea_room_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    image_url = Column(Unicode(1000), nullable=False)
    is_primary = Column(Boolean, nullable=False, default=False)
    order_index = Column(Integer, nullable=False, default=0)

    tea_room = relationship("TeaRoom", back_populates="images")


class TeaRoomSource(Base):
    """Nguồn dữ liệu. `source_name` là tên nguồn trong crawler_config (JSON gọi là `domain`)."""
    __tablename__ = "tea_room_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    source_name = Column(Unicode(100), nullable=False)
    source_url = Column(Unicode(1000), nullable=True)  # để dẫn nguồn khi hiển thị
    raw_name = Column(Unicode(500), nullable=True)
    crawled_at = Column(DateTime, nullable=True)

    tea_room = relationship("TeaRoom", back_populates="sources")

    __table_args__ = (
        Index("idx_source_name", "source_name"),
    )


class TeaRoomTag(Base):
    """Tag: music_genres / atmosphere_tags / features."""
    __tablename__ = "tea_room_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tea_room_id = Column(Integer, ForeignKey("tea_rooms.id", ondelete="CASCADE"), nullable=False)
    tag_type = Column(Unicode(50), nullable=False)
    tag_value = Column(Unicode(100), nullable=False)

    tea_room = relationship("TeaRoom", back_populates="tags")

    __table_args__ = (
        UniqueConstraint("tea_room_id", "tag_type", "tag_value", name="uq_tag_per_room"),
        Index("idx_tag_type_value", "tag_type", "tag_value"),
    )