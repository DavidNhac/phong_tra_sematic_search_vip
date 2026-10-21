"""Dựng văn bản embedding và encode bằng sentence-transformers (mặc định BAAI/bge-m3)."""
from __future__ import annotations

import statistics

import structlog

from src.database.client import settings

logger = structlog.get_logger()

# Nhãn dễ đọc cho tag (văn bản embed là tiếng Việt tự nhiên, không phải mã tag)
TAG_LABELS: dict[str, dict[str, str]] = {
    "music_genres": {
        "nhac_trinh": "nhạc Trịnh", "acoustic": "acoustic", "jazz": "jazz",
        "bolero": "bolero", "lofi_chill": "lofi chill", "indie": "indie",
        "traditional": "nhạc truyền thống", "piano": "piano", "pop_ballad": "pop ballad",
    },
    "atmosphere_tags": {
        "yen_tinh": "yên tĩnh", "lang_man": "lãng mạn", "co_kinh": "cổ kính",
        "view_dep": "view đẹp", "san_vuon": "sân vườn", "rooftop": "rooftop",
        "sang_trong": "sang trọng", "binh_dan": "bình dân", "tre_trung": "trẻ trung",
        "co_dien": "cổ điển",
    },
    "features": {
        "co_live_music": "có nhạc sống", "cho_dat_cho": "có thể đặt chỗ",
        "do_an_nhe": "có đồ ăn nhẹ", "parking": "có chỗ đỗ xe",
        "khong_thuoc_la": "không hút thuốc", "wifi_manh": "wifi mạnh",
    },
}
_TAG_PREFIX = {
    "music_genres": "Thể loại nhạc",
    "atmosphere_tags": "Không gian",
    "features": "Tiện ích",
}


def build_text(
    name: str,
    district: str | None,
    tags: dict[str, list[str]],
    description: str,
    include_tags: bool = True,
) -> str:
    """Tên + khu vực + tag (chữ dễ đọc) + mô tả, để mỗi đoạn tự đủ ngữ cảnh."""
    parts = [f"{name}."]
    if district:
        parts.append(f"Khu vực: {district}.")
    if include_tags:
        for tag_type, prefix in _TAG_PREFIX.items():
            values = tags.get(tag_type) or []
            if values:
                labels = ", ".join(TAG_LABELS[tag_type].get(v, v) for v in values)
                parts.append(f"{prefix}: {labels}.")
    parts.append(description.strip())
    return " ".join(parts)


class Embedder:
    """Bọc SentenceTransformer, tải model lười (lần đầu tải bge-m3 khoảng vài GB)."""

    def __init__(self, model_name: str | None = None, device: str | None = None):
        self.model_name = model_name or settings.embedding_model
        self.device = device or settings.embedding_device
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("loading_model", model=self.model_name, device=self.device)
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def encode(self, texts: list[str], batch_size: int | None = None) -> list[list[float]]:
        """Encode theo lô, vector đã chuẩn hóa (hợp với khoảng cách cosine)."""
        vectors = self.model.encode(
            texts,
            batch_size=batch_size or settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=True,
            convert_to_numpy=True,
        )
        return vectors.tolist()

    def token_stats(self, texts: list[str]) -> dict[str, int | float]:
        """Số token của từng văn bản so với giới hạn của model."""
        ids = self.model.tokenizer(texts, add_special_tokens=True)["input_ids"]
        lengths = [len(x) for x in ids]
        return {
            "count": len(lengths),
            "max": max(lengths),
            "mean": round(statistics.mean(lengths), 1),
            "limit": int(self.model.max_seq_length),
        }