"""Pydantic models for Qdrant payload schema."""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum


class MusicGenre(str, Enum):
    """Music genre taxonomy."""
    NHAC_TRINH = "nhac_trinh"
    ACOUSTIC = "acoustic"
    JAZZ = "jazz"
    BOLERO = "bolero"
    LOFI_CHILL = "lofi_chill"
    INDIE = "indie"
    TRADITIONAL = "traditional"
    PIANO = "piano"


class AtmosphereTag(str, Enum):
    """Atmosphere/space tags."""
    YEN_TINH = "yen_tinh"
    LANG_MAN = "lang_man"
    CO_KINH = "co_kinh"
    VIEW_HO = "view_ho"
    SAN_VUON = "san_vuon"
    ROOFTOP = "rooftop"
    SANG_TRONG = "sang_trong"
    BINH_DAN = "binh_dan"


class Feature(str, Enum):
    """Features/amenities."""
    CO_LIVE_MUSIC = "co_live_music"
    CHO_DAT_CHO = "cho_dat_cho"
    DO_AN_NHE = "do_an_nhe"
    PARKING = "parking"
    KHONG_THUOC_LA = "khong_thuoc_la"


class SourceInfo(BaseModel):
    """Information about data source."""
    url: str
    domain: str
    crawled_at: datetime = Field(default_factory=datetime.now)


class PhongTraPayload(BaseModel):
    """
    Payload schema for Qdrant points.
    Each tea room entity will have this structure.
    """
    
    # === Identification ===
    entity_id: str = Field(..., description="Unique ID after entity resolution")
    name: str = Field(..., description="Primary name")
    aliases: List[str] = Field(default_factory=list, description="Alternative names from different sources")
    
    # === Location (for filtering) ===
    address: str = Field(..., description="Full address")
    district: str = Field(..., description="District name (e.g., 'Tây Hồ', 'Hoàn Kiếm')")
    ward: Optional[str] = Field(None, description="Ward name")
    latitude: Optional[float] = Field(None, description="GPS latitude")
    longitude: Optional[float] = Field(None, description="GPS longitude")
    
    # === Categories (for filtering) ===
    music_genres: List[str] = Field(default_factory=list, description="Music genres")
    atmosphere_tags: List[str] = Field(default_factory=list, description="Atmosphere tags")
    features: List[str] = Field(default_factory=list, description="Features/amenities")
    
    # === Price (for filtering) ===
    price_min_vnd: Optional[int] = Field(None, description="Minimum price in VND")
    price_max_vnd: Optional[int] = Field(None, description="Maximum price in VND")
    price_label: str = Field("", description="Price range label (e.g., 'dưới_200k', '200k-500k')")
    
    # === Time ===
    open_hours: str = Field("", description="Opening hours (e.g., '08:00 - 22:00')")
    open_days: List[str] = Field(default_factory=list, description="Open days (e.g., ['T2', 'T3', ..., 'CN'])")
    has_live_music_schedule: bool = Field(False, description="Has live music schedule")
    
    # === Reviews ===
    rating: Optional[float] = Field(None, description="Average rating (0-5)")
    review_count: int = Field(0, description="Number of reviews")
    
    # === Data Sources (solve duplicate problem) ===
    sources: List[SourceInfo] = Field(default_factory=list, description="List of source URLs")
    primary_source: str = Field("", description="Primary source URL")
    
    # === System Metadata ===
    last_updated: datetime = Field(default_factory=datetime.now)
    data_quality_score: float = Field(0.0, description="Data completeness score (0-1)")
    
    # === Text for Embedding (not stored in Qdrant, used for indexing) ===
    text_overview: str = Field("", description="Text for overview aspect embedding")
    text_atmosphere: str = Field("", description="Text for atmosphere aspect embedding")
    text_experience: str = Field("", description="Text for experience aspect embedding")
    
    class Config:
        json_schema_extra = {
            "example": {
                "entity_id": "abc123-def456",
                "name": "Phòng trà Cafe Sáng - Nhạc Trịnh",
                "aliases": ["Cafe Sáng", "Phòng trà Cafe Sáng"],
                "address": "123 Xuân Diệu, Tây Hồ, Hà Nội",
                "district": "Tây Hồ",
                "music_genres": ["nhac_trinh", "acoustic"],
                "atmosphere_tags": ["yen_tinh", "view_ho"],
                "price_min_vnd": 150000,
                "price_max_vnd": 300000,
                "rating": 4.5,
                "sources": [
                    {"url": "https://mytour.vn/...", "domain": "mytour.vn"}
                ]
            }
        }


class SearchResult(BaseModel):
    """Search result model."""
    entity_id: str
    name: str
    address: str
    district: str
    score: float
    payload: PhongTraPayload