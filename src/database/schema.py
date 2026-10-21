"""Pydantic models cho payload Qdrant (mỗi điểm = một mô tả của một quán)."""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class MusicGenre(str, Enum):
    """Music genre taxonomy (khớp llm_tagger)."""
    NHAC_TRINH = "nhac_trinh"
    ACOUSTIC = "acoustic"
    JAZZ = "jazz"
    BOLERO = "bolero"
    LOFI_CHILL = "lofi_chill"
    INDIE = "indie"
    TRADITIONAL = "traditional"
    PIANO = "piano"
    POP_BALLAD = "pop_ballad"


class AtmosphereTag(str, Enum):
    """Atmosphere/space tags."""
    YEN_TINH = "yen_tinh"
    LANG_MAN = "lang_man"
    CO_KINH = "co_kinh"
    VIEW_DEP = "view_dep"
    SAN_VUON = "san_vuon"
    ROOFTOP = "rooftop"
    SANG_TRONG = "sang_trong"
    BINH_DAN = "binh_dan"
    TRE_TRUNG = "tre_trung"
    CO_DIEN = "co_dien"


class Feature(str, Enum):
    """Features/amenities."""
    CO_LIVE_MUSIC = "co_live_music"
    CHO_DAT_CHO = "cho_dat_cho"
    DO_AN_NHE = "do_an_nhe"
    PARKING = "parking"
    KHONG_THUOC_LA = "khong_thuoc_la"
    WIFI_MANH = "wifi_manh"


class PhongTraPayload(BaseModel):
    """
    Payload của một điểm Qdrant = một mô tả (tea_room_descriptions) của một quán.
    Dữ liệu quán lấy từ SQL Server (nguồn sự thật); các trường thiếu là None / [].
    """

    # === Định danh ===
    tea_room_id: int = Field(..., description="tea_rooms.id, dùng để gộp kết quả theo quán")
    description_id: int = Field(..., description="tea_room_descriptions.id")
    slug: str
    name: str

    # === Vị trí ===
    address: Optional[str] = None
    district: Optional[str] = None

    # === Phân loại (để lọc) ===
    music_genres: List[str] = Field(default_factory=list)
    atmosphere_tags: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)

    # === Giá, giờ ===
    price_min_vnd: Optional[int] = None
    price_max_vnd: Optional[int] = None
    hours: Optional[str] = None

    # === Hiển thị ===
    image_url: Optional[str] = None

    # === Nguồn của đoạn mô tả này (để dẫn nguồn) ===
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    text: str = Field(..., description="Nội dung mô tả gốc của nguồn")


class SearchResult(BaseModel):
    """Một quán trong kết quả tìm kiếm (đã gộp theo quán)."""
    tea_room_id: int
    slug: str
    name: str
    address: Optional[str] = None
    district: Optional[str] = None
    score: float
    payload: PhongTraPayload