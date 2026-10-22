"""Test phần dựng bộ lọc (không cần Qdrant, không tải model). python -m pytest tests/unit/test_searcher.py -v"""
import pytest

from src.search.searcher import RRF_K, build_filter, rrf_fuse


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

def test_rrf_quan_dung_dau_ca_hai_nhanh_thang():
    scores = rrf_fuse([[1, 2, 3], [1, 3, 2]])
    assert max(scores, key=scores.get) == 1
    assert scores[1] == pytest.approx(2 / (RRF_K + 1))


def test_rrf_quan_chi_co_o_mot_nhanh_van_duoc_tinh():
    scores = rrf_fuse([[1, 2], [3]])
    assert set(scores) == {1, 2, 3}
    assert scores[3] == pytest.approx(1 / (RRF_K + 1))