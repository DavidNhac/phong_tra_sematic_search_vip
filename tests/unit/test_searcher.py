"""Test phần dựng bộ lọc (không cần Qdrant, không tải model). python -m pytest tests/unit/test_searcher.py -v"""
import pytest

from src.search.searcher import build_filter


def test_khong_loc_tra_ve_none():
    assert build_filter() is None


def test_loc_day_du():
    flt = build_filter(district="Cầu Giấy", max_price=50000, tags=["nhac_trinh", "yen_tinh", "co_live_music"])
    keys = [c.key for c in flt.must]
    assert keys == ["district", "price_min_vnd", "music_genres", "atmosphere_tags", "features"]
    assert flt.must[1].range.lte == 50000


def test_tag_khong_hop_le():
    with pytest.raises(ValueError, match="Tag không hợp lệ"):
        build_filter(tags=["khong_co_tag_nay"])