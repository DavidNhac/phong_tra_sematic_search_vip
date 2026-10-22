"""Sparse vector kiểu BM25 cho tiếng Việt, không cần thư viện ngoài.

Vì sao tự viết: bộ stemmer/stopword của BM25 có sẵn thường dành cho tiếng Anh. Tiếng Việt
tách từ theo âm tiết (khoảng trắng) nên chỉ cần: bỏ dấu, hạ chữ thường, lấy âm tiết đơn
và cặp âm tiết liền kề ("nhac trinh", "san vuon").

Bỏ dấu có chủ ý: người dùng hay gõ không dấu; nhánh dense lo phần nghĩa và phân biệt dấu.
Điểm IDF do Qdrant tính (modifier=IDF khi tạo collection), ở đây chỉ tạo trọng số TF.
"""
from __future__ import annotations

import re
import unicodedata
import zlib
from collections import Counter

K1 = 1.2  # độ bão hòa TF (BM25), không chuẩn hóa độ dài vì mỗi đoạn có độ dài gần nhau
_WORD = re.compile(r"\w+", re.UNICODE)


def strip_accents(text: str) -> str:
    """Hạ chữ thường, bỏ dấu tiếng Việt (đ -> d)."""
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def tokenize(text: str) -> list[str]:
    """Âm tiết đơn + cặp âm tiết liền kề, đã bỏ dấu."""
    words = _WORD.findall(strip_accents(unicodedata.normalize("NFC", text)))
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


def _token_id(token: str) -> int:
    """Id ổn định giữa các lần chạy (hash() của Python thì không)."""
    return zlib.crc32(token.encode("utf-8"))


def sparse_parts(text: str, is_query: bool = False) -> tuple[list[int], list[float]]:
    """(indices, values). Tài liệu: TF bão hòa. Câu hỏi: mỗi token duy nhất = 1.0."""
    counts = Counter(_token_id(t) for t in tokenize(text))
    indices = sorted(counts)
    if is_query:
        return indices, [1.0] * len(indices)
    return indices, [counts[i] * (K1 + 1) / (counts[i] + K1) for i in indices]


def encode_sparse(texts: list[str], is_query: bool = False):
    """Danh sách qdrant SparseVector (import lười để module test được không cần qdrant)."""
    from qdrant_client.models import SparseVector

    out = []
    for text in texts:
        idx, val = sparse_parts(text, is_query)
        out.append(SparseVector(indices=idx, values=val))
    return out