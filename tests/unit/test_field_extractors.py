"""Tests for the description cleaning in field_extractors (bare address lines)."""
from src.processing import field_extractors as fx

MULTI_BRANCH = (
    "Tầng 1 N6E Trung Hoà, Nhân Chính, Hà Nội\n"
    "Số A2 Lô BT1A Bùi Xuân Phái, Hà Nội\n"
    "Số 54 – BT2, Nguyễn Khuyến, Hà Nội\n"
    "Bạn đang tìm một quán cafe đẹp ở Hà Nội, không gian acoustic nhẹ nhàng."
)


def test_dong_dia_chi_tran_bi_bo_khoi_mo_ta():
    desc = fx.build_description(MULTI_BRANCH)
    assert desc.startswith("Bạn đang tìm một quán cafe")
    assert "N6E" not in desc and "Nguyễn Khuyến" not in desc


def test_dia_chi_van_duoc_trich_tu_raw_text():
    assert fx.extract_address(MULTI_BRANCH) == "Tầng 1 N6E Trung Hoà, Nhân Chính, Hà Nội"


def test_dong_nhan_dia_chi_va_gia_xu_ly_nhu_cu():
    raw = (
        "Địa chỉ: 36 Cát Linh, Q. Đống Đa, Hà Nội\n"
        "Giá từ: 40.000đ – 100.000đ. Polygon Musik là một phòng trà nổi tiếng."
    )
    assert fx.build_description(raw) == "Polygon Musik là một phòng trà nổi tiếng."


def test_cau_van_khong_bi_cat_nham():
    for sentence in (
        "Số lượng khách tại Hà Nội, rất đông vào cuối tuần.",  # starts with "Số" but no digit
        "Số 5 quán nổi bật nhất Hà Nội, theo bình chọn. Quán rất đẹp.",  # a sentence
        "Quán nằm ở số 15 ngõ 8, Hà Nội, rất yên tĩnh.",  # address inside a sentence
    ):
        assert fx.build_description(sentence) == sentence