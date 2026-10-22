"""Test sparse BM25 tiếng Việt (không cần Qdrant)."""
from src.embeddings import sparse


def test_hoa_thuong_va_dau_khong_anh_huong():
    assert sparse.sparse_parts("Nhạc TRỊNH") == sparse.sparse_parts("nhac trinh")


def test_co_cap_am_tiet_va_chu_d():
    toks = sparse.tokenize("Sân vườn đẹp")
    assert "san_vuon" in toks and "dep" in toks


def test_id_on_dinh():
    assert sparse.sparse_parts("rooftop")[0] == sparse.sparse_parts("rooftop")[0]


def test_tf_bao_hoa_va_cau_hoi_bang_1():
    _, doc = sparse.sparse_parts("jazz jazz jazz jazz")
    _, one = sparse.sparse_parts("jazz")
    assert max(doc) < 2.2 and max(one) > 0
    _, q = sparse.sparse_parts("jazz jazz", is_query=True)
    assert set(q) == {1.0}