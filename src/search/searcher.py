"""Tìm kiếm ngữ nghĩa: embed câu hỏi, tìm vector, lọc, gộp theo quán.

Chạy thử từ thư mục gốc repo:
    python -m src.search.searcher "quán nhạc Trịnh yên tĩnh"
    python -m src.search.searcher "cafe acoustic" --district "Cầu Giấy" --max-price 100000 --tag acoustic
    python -m src.search.searcher                      # chế độ hỏi liên tục (chỉ tải model một lần)
"""
from __future__ import annotations

import argparse
import sys

import structlog
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

from src.database.client import get_qdrant_client, settings
from src.database.schema import PhongTraPayload, SearchResult
from src.embeddings.embedder import TAG_LABELS, Embedder

logger = structlog.get_logger()
DEFAULT_MIN_SCORE = 0.45  # dưới mức này là gần như không liên quan (câu lạc đề cho ~0.35-0.37)

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

    def search(
        self,
        query: str,
        limit: int = 5,
        district: str | None = None,
        max_price: int | None = None,
        tags: list[str] | None = None,
        min_score: float | None = DEFAULT_MIN_SCORE,
    ) -> list[SearchResult]:
        """Trả về tối đa `limit` quán, mỗi quán một lần, kèm đoạn mô tả khớp nhất."""
        query = query.strip()
        if not query:
            raise ValueError("Câu hỏi rỗng")

        vector = self.embedder.encode([query], show_progress_bar=False)[0]
        
        response = self.client.query_points_groups(
            collection_name=settings.qdrant_collection,
            query=vector,
            group_by="tea_room_id",
            group_size=1,
            limit=limit,
            query_filter=build_filter(district, max_price, tags),
            score_threshold=min_score,
            with_payload=True,
        )

        results: list[SearchResult] = []
        for group in response.groups:
            hit = group.hits[0]
            payload = PhongTraPayload(**hit.payload)
            results.append(SearchResult(
                tea_room_id=payload.tea_room_id,
                slug=payload.slug,
                name=payload.name,
                address=payload.address,
                district=payload.district,
                score=hit.score,
                payload=payload,
            ))
        return results


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
    args = parser.parse_args(argv)

    try:
        build_filter(args.district, args.max_price, args.tag)  # kiểm tra tag trước khi tải model
    except ValueError as exc:
        print(exc)
        return 1

    searcher = Searcher()

    def run(q: str) -> None:
        _print_results(searcher.search(
            q, args.limit, args.district, args.max_price, args.tag, args.min_score
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