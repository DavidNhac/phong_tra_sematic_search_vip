"""Tìm kiếm lai (hybrid): vector ngữ nghĩa (dense) + từ khóa (sparse BM25), gộp bằng RRF.

Chạy thử từ thư mục gốc repo:
    python -m src.search.searcher "quán nhạc Trịnh yên tĩnh"
    python -m src.search.searcher "cafe acoustic" --district "Cầu Giấy" --max-price 100000 --tag acoustic
    python -m src.search.searcher "Trịnh Ca" --mode dense   # chỉ vector, để so sánh
    python -m src.search.searcher                      # chế độ hỏi liên tục (chỉ tải model một lần)

Điểm hiển thị: mode dense = cosine; mode hybrid = điểm RRF chuẩn hóa 0-1 (1.0 = đứng đầu cả
hai nhánh), KHÔNG so sánh trực tiếp với cosine.
"""
from __future__ import annotations

import argparse
import sys

import structlog
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

from src.database.client import get_qdrant_client, settings
from src.database.indexer import DENSE_VECTOR, SPARSE_VECTOR
from src.database.schema import PhongTraPayload, SearchResult
from src.embeddings.embedder import TAG_LABELS, Embedder
from src.embeddings.sparse import encode_sparse

logger = structlog.get_logger()
DEFAULT_MIN_SCORE = 0.45  # ngưỡng cosine của nhánh dense (câu lạc đề cho ~0.35-0.37)
MODES = ("hybrid", "dense")
RRF_K = 60  # hằng số RRF chuẩn
MIN_POOL = 30  # số quán lấy từ mỗi nhánh trước khi gộp (dữ liệu nhỏ nên lấy rộng)


def rrf_fuse(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: điểm = tổng 1/(k + hạng) qua các danh sách (hạng bắt đầu từ 1)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def _tag_field(tag: str) -> str:
    """Tag thuộc nhóm nào (music_genres / atmosphere_tags / features)."""
    for field, labels in TAG_LABELS.items():
        if tag in labels:
            return field
    valid = sorted(t for labels in TAG_LABELS.values() for t in labels)
    raise ValueError(f"Tag không hợp lệ: {tag!r}. Tag hợp lệ: {', '.join(valid)}")


def build_filter(
    district: str | None = None,
    max_price: int | None = None,
    tags: list[str] | None = None,
) -> Filter | None:
    """Điều kiện lọc (AND). max_price: giá thấp nhất của quán <= max_price.

    Quán không có thông tin giá sẽ bị loại khi dùng max_price.
    """
    must: list[FieldCondition] = []
    if district:
        must.append(FieldCondition(key="district", match=MatchValue(value=district)))
    if max_price is not None:
        must.append(FieldCondition(key="price_min_vnd", range=Range(lte=max_price)))
    for tag in tags or []:
        must.append(FieldCondition(key=_tag_field(tag), match=MatchValue(value=tag)))
    return Filter(must=must) if must else None


class Searcher:
    """Giữ sẵn model và client để hỏi nhiều lần."""

    def __init__(self, embedder: Embedder | None = None, client: QdrantClient | None = None):
        self.embedder = embedder or Embedder()
        self.client = client or get_qdrant_client()

    def _dense(self, vector, pool, flt, min_score):
        """Nhánh vector: (payload, cosine) mỗi quán một đoạn khớp nhất, đã sắp giảm dần."""
        response = self.client.query_points_groups(
            collection_name=settings.qdrant_collection,
            query=vector,
            using=DENSE_VECTOR,
            group_by="tea_room_id",
            group_size=1,
            limit=pool,
            query_filter=flt,
            score_threshold=min_score,
            with_payload=True,
        )
        return [(g.hits[0].payload, g.hits[0].score) for g in response.groups]

    def _sparse(self, query, pool, flt):
        """Nhánh từ khóa: payload mỗi quán một lần (đoạn có điểm BM25 cao nhất)."""
        sv = encode_sparse([query], is_query=True)[0]
        if not sv.indices:
            return []
        response = self.client.query_points(
            collection_name=settings.qdrant_collection,
            query=sv,
            using=SPARSE_VECTOR,
            query_filter=flt,
            limit=pool * 3,  # nhiều đoạn/quán nên lấy dư rồi gộp theo quán
            with_payload=True,
        )
        seen: dict[int, dict] = {}
        for hit in response.points:
            seen.setdefault(hit.payload["tea_room_id"], hit.payload)
        return list(seen.values())[:pool]

    @staticmethod
    def _to_result(payload_dict: dict, score: float) -> SearchResult:
        payload = PhongTraPayload(**payload_dict)
        return SearchResult(
            tea_room_id=payload.tea_room_id,
            slug=payload.slug,
            name=payload.name,
            address=payload.address,
            district=payload.district,
            score=score,
            payload=payload,
        )

    def search(
        self,
        query: str,
        limit: int = 5,
        district: str | None = None,
        max_price: int | None = None,
        tags: list[str] | None = None,
        min_score: float | None = DEFAULT_MIN_SCORE,
        mode: str = "hybrid",
    ) -> list[SearchResult]:
        """Trả về tối đa `limit` quán, mỗi quán một lần, kèm đoạn mô tả khớp nhất.

        mode="dense": chỉ vector (như trước). mode="hybrid": gộp vector + từ khóa bằng RRF.
        min_score là ngưỡng cosine của nhánh dense: quán không qua ngưỡng bị loại khỏi cả
        kết quả hybrid (để câu lạc đề không lọt vào chỉ vì trùng vài từ phổ biến).
        """
        query = query.strip()
        if not query:
            raise ValueError("Câu hỏi rỗng")
        if mode not in MODES:
            raise ValueError(f"mode phải là một trong {MODES}")

        flt = build_filter(district, max_price, tags)
        pool = max(limit * 4, MIN_POOL)
        vector = self.embedder.encode([query], show_progress_bar=False)[0]
        dense = self._dense(vector, pool, flt, min_score)

        if mode == "dense":
            return [self._to_result(p, s) for p, s in dense[:limit]]

        sparse = self._sparse(query, pool, flt)
        payloads = {p["tea_room_id"]: p for p, _ in dense}
        for p in sparse:
            payloads.setdefault(p["tea_room_id"], p)
        dense_ids = [p["tea_room_id"] for p, _ in dense]
        rankings = [r for r in (dense_ids, [p["tea_room_id"] for p in sparse]) if r]
        fused = rrf_fuse(rankings)
        ordered = sorted(fused, key=fused.get, reverse=True)
        if min_score is not None:
            allowed = set(dense_ids)
            ordered = [i for i in ordered if i in allowed]
        best_possible = len(rankings) / (RRF_K + 1)  # hạng 1 ở mọi nhánh
        return [
            self._to_result(payloads[i], fused[i] / best_possible) for i in ordered[:limit]
        ]


def _print_results(results: list[SearchResult]) -> None:
    if not results:
        print("(không có kết quả)")
        return
    for rank, r in enumerate(results, start=1):
        p = r.payload
        price = f"{p.price_min_vnd}-{p.price_max_vnd}" if p.price_min_vnd is not None else "?"
        print(f"{rank}. [{r.score:.3f}] {r.name} | {r.district or '?'} | giá {price} | {p.hours or '?'}")
        print(f"   tag: {p.music_genres} {p.atmosphere_tags} {p.features}")
        snippet = p.text[:160].replace("\n", " ")
        print(f"   ({p.source_name}) {snippet}...")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Tìm quán bằng câu hỏi tự nhiên.")
    parser.add_argument("query", nargs="?", help="Bỏ trống để vào chế độ hỏi liên tục")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--district", default=None)
    parser.add_argument("--max-price", type=int, default=None, help="VNĐ; giá thấp nhất của quán <= giá trị này")
    parser.add_argument("--tag", action="append", default=[], help="Lặp lại để lọc nhiều tag (AND)")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--mode", choices=MODES, default="hybrid", help="hybrid = vector + từ khóa")
    args = parser.parse_args(argv)

    try:
        build_filter(args.district, args.max_price, args.tag)  # kiểm tra tag trước khi tải model
    except ValueError as exc:
        print(exc)
        return 1

    searcher = Searcher()

    def run(q: str) -> None:
        _print_results(searcher.search(
            q, args.limit, args.district, args.max_price, args.tag, args.min_score, args.mode
        ))

    if args.query:
        run(args.query)
        return 0

    print("Nhập câu hỏi (Enter trống để thoát). Bộ lọc lấy từ tham số dòng lệnh.")
    while True:
        try:
            q = input("\n> ").strip()
        except EOFError:
            break
        if not q:
            break
        run(q)
    return 0


if __name__ == "__main__":
    sys.exit(main())