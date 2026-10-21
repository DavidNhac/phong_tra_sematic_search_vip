"""Test phần thuần logic (không tải model, không cần Qdrant). python -m pytest tests/unit/test_embedder.py -v"""
from src.database.schema import PhongTraPayload
from src.embeddings.embedder import build_text

TAGS = {
    "music_genres": ["nhac_trinh"],
    "atmosphere_tags": ["yen_tinh", "co_kinh"],
    "features": ["co_live_music"],
}


def test_build_text_day_du():
    text = build_text("Phòng trà Trịnh Ca", "Cầu Giấy", TAGS, " Mô tả. ")
    assert text.startswith("Phòng trà Trịnh Ca. Khu vực: Cầu Giấy.")
    assert "Thể loại nhạc: nhạc Trịnh." in text
    assert "Không gian: yên tĩnh, cổ kính." in text
    assert "Tiện ích: có nhạc sống." in text
    assert text.endswith("Mô tả.")


def test_build_text_khong_tag_va_thieu_quan():
    text = build_text("Quán A", None, TAGS, "Mô tả.", include_tags=False)
    assert text == "Quán A. Mô tả."


def test_build_text_tag_la_giu_nguyen_ma():
    text = build_text("Quán A", "", {"features": ["tag_moi"]}, "Mô tả.")
    assert "Tiện ích: tag_moi." in text


def test_payload_chap_nhan_thieu_dia_chi_quan():
    p = PhongTraPayload(
        tea_room_id=1, description_id=2, slug="quan-a", name="Quán A", text="Mô tả.",
    )
    assert p.address is None and p.district is None
    assert p.music_genres == [] and p.price_min_vnd is None