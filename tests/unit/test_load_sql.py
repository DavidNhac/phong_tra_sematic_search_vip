"""Test nạp SQL bằng SQLite tạm. Chạy: python -m pytest tests/test_load_sql.py -v"""
from datetime import datetime

import pytest
from sqlalchemy import func, select

from src.database.load_sql import load_entities
from src.database.sql_client import SQLClient
from src.database.sql_models import (
    TeaRoom, TeaRoomAlias, TeaRoomDescription, TeaRoomImage, TeaRoomSource, TeaRoomTag,
)

# Rút gọn từ object thật của Phòng trà Trịnh Ca
TRINH_CA = {
    "id": "phong-tra-trinh-ca",
    "name": "Phòng trà Trịnh Ca",
    "aliases": ["Phòng Trà Trịnh Ca", "Phòng trà Trịnh Ca Hà Nội", "Trịnh Ca Hà Nội"],
    "address": "Ngõ 233 Tô Hiệu, Cầu Giấy, Hà Nội",
    "district": "Cầu Giấy",
    "price": "20.000 - 55.000 VNĐ",
    "price_min": 20000,
    "price_max": 55000,
    "hours": "07:00 - 23:00",
    "description": "Nằm khuất sâu trong con ngõ 233 của phố Tô Hiệu.",
    "descriptions": [
        {"source": "digiticket", "language": "vi", "text": "Nằm khuất sâu trong con ngõ 233."},
        {"source": "mytour_2", "language": "vi", "text": "Trịnh Ca Hà Nội là địa điểm lý tưởng."},
    ],
    "image_url": "https://example.com/a.jpg",
    "image_urls": ["https://example.com/a.jpg", "https://example.com/b.jpg"],
    "sources": [
        {"domain": "digiticket", "source_url": "https://digiticket.vn/blog/phong-tra-ha-noi",
         "raw_name": "1. Phòng trà Trịnh Ca", "crawled_at": "2026-10-21T12:26:52"},
        {"domain": "mytour_2", "source_url": "https://mytour.vn/x.html",
         "raw_name": "2. Trịnh Ca Hà Nội", "crawled_at": "2026-10-21T12:26:49"},
    ],
    "source_count": 2,
    "music_genres": ["nhac_trinh", "acoustic"],
    "atmosphere_tags": ["yen_tinh", "co_kinh"],
    "features": ["co_live_music", "do_an_nhe"],
}

# Quán thiếu gần hết trường: chuỗi rỗng / null phải thành NULL
MINIMAL = {
    "id": "quan-toi-gian", "name": "Quán tối giản", "aliases": [], "address": "", "district": "",
    "price": "", "price_min": None, "price_max": None, "hours": "",
    "description": "Mô tả ngắn.", "descriptions": [], "image_url": "", "image_urls": [],
    "sources": [], "source_count": 1,
    "music_genres": [], "atmosphere_tags": [], "features": [],
}


@pytest.fixture
def client(tmp_path):
    c = SQLClient(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    c.create_tables()
    yield c
    c.engine.dispose()


def _count(client, model):
    with client.get_session() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_nap_hai_lan_khong_nhan_doi(client):
    first = load_entities(client, [TRINH_CA, MINIMAL])
    second = load_entities(client, [TRINH_CA, MINIMAL])
    assert first == {"created": 2, "updated": 0, "deleted": 0}
    assert second == {"created": 0, "updated": 2, "deleted": 0}
    assert _count(client, TeaRoom) == 2
    assert _count(client, TeaRoomAlias) == 3
    assert _count(client, TeaRoomDescription) == 2
    assert _count(client, TeaRoomImage) == 2
    assert _count(client, TeaRoomSource) == 2
    assert _count(client, TeaRoomTag) == 6


def test_tieng_viet_va_ngay_gio(client):
    load_entities(client, [TRINH_CA])
    with client.get_session() as s:
        room = s.scalar(select(TeaRoom).where(TeaRoom.slug == "phong-tra-trinh-ca"))
        assert room.name == "Phòng trà Trịnh Ca"
        assert room.district == "Cầu Giấy"
        assert room.price_min == 20000 and room.price_max == 55000
        assert room.image_url == "https://example.com/a.jpg"
        assert room.images[0].is_primary and not room.images[1].is_primary
        assert all(isinstance(src.crawled_at, datetime) for src in room.sources)


def test_rong_thanh_null(client):
    load_entities(client, [MINIMAL])
    with client.get_session() as s:
        room = s.scalar(select(TeaRoom).where(TeaRoom.slug == "quan-toi-gian"))
        assert room.address is None and room.district is None
        assert room.price is None and room.hours is None
        assert room.price_min is None and room.image_url is None
        assert room.description == "Mô tả ngắn."


def test_cap_nhat_thay_the_bang_con(client):
    load_entities(client, [TRINH_CA])
    load_entities(client, [dict(TRINH_CA, features=["wifi_manh"], aliases=[])])
    with client.get_session() as s:
        tags = {(t.tag_type, t.tag_value) for t in s.scalars(select(TeaRoomTag))}
    assert ("features", "wifi_manh") in tags
    assert ("features", "do_an_nhe") not in tags
    assert _count(client, TeaRoomAlias) == 0


def test_prune(client):
    load_entities(client, [TRINH_CA, MINIMAL])
    stats = load_entities(client, [TRINH_CA], prune=True)
    assert stats["deleted"] == 1
    assert _count(client, TeaRoom) == 1
    assert _count(client, TeaRoomTag) == 6


def test_id_trung_hoac_prune_rong_bi_tu_choi(client):
    with pytest.raises(ValueError):
        load_entities(client, [TRINH_CA, TRINH_CA])
    with pytest.raises(ValueError):
        load_entities(client, [], prune=True)