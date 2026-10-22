"""So sánh dense / hybrid / hybrid không cổng cosine trên bộ câu hỏi có đáp án.

Chạy từ thư mục gốc repo:
    python -m src.search.eval_search
    python -m src.search.eval_search --queries tests/eval/queries.json --limit 5 --verbose

Mỗi câu trong queries.json có: query, expected (tên quán chứa chuỗi này), must_not (không được
xuất hiện trong top), note. Khớp theo chuỗi con, không phân biệt hoa/thường.

Chỉ đọc, không ghi gì vào SQL hay Qdrant.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

DEFAULT_QUERIES = Path("tests/eval/queries.json")

# (nhãn, mode, min_score) - 0.45 là DEFAULT_MIN_SCORE của searcher
CONFIGS: list[tuple[str, str, float]] = [
    ("dense", "dense", 0.45),
    ("hybrid", "hybrid", 0.45),
    ("hybrid-min0", "hybrid", 0.0),
]


def _norm(text: str) -> str:
    """Chuẩn hóa Unicode (NFC) và hạ chữ thường để so khớp tên."""
    return unicodedata.normalize("NFC", text).casefold()


def _first_rank(names: list[str], needle: str) -> int | None:
    """Hạng (bắt đầu từ 1) của quán đầu tiên có tên chứa `needle`, không có thì None."""
    target = _norm(needle)
    for rank, name in enumerate(names, start=1):
        if target in _norm(name):
            return rank
    return None


def evaluate_query(names: list[str], expected: list[str], must_not: list[str]) -> dict:
    """Tính chỉ số cho một câu từ danh sách tên quán đã xếp hạng (top-k).

    recall: tỉ lệ đáp án mong đợi nằm trong top-k.
    rr: nghịch đảo hạng của đáp án đúng đầu tiên (0 nếu không có).
    violations: các chuỗi must_not xuất hiện trong top-k.
    """
    ranks = {e: _first_rank(names, e) for e in expected}
    found = [r for r in ranks.values() if r is not None]
    recall = len(found) / len(expected) if expected else None
    rr = (1.0 / min(found)) if found else 0.0
    violations = [m for m in must_not if _first_rank(names, m) is not None]
    return {"ranks": ranks, "recall": recall, "rr": rr, "violations": violations}


def load_queries(path: Path) -> list[dict]:
    """Đọc và kiểm tra file câu hỏi."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for i, item in enumerate(data):
        if not item.get("query"):
            raise ValueError(f"Mục {i} thiếu 'query'")
        item.setdefault("expected", [])
        item.setdefault("must_not", [])
    return data


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Đánh giá dense vs hybrid.")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--limit", type=int, default=5, help="Top-k để chấm")
    parser.add_argument("--verbose", action="store_true", help="In danh sách top-k của từng câu")
    args = parser.parse_args(argv)

    queries = load_queries(args.queries)

    from src.search.searcher import Searcher  # import muộn: tải model nặng

    searcher = Searcher()
    summary: dict[str, dict[str, float]] = {}

    for label, mode, min_score in CONFIGS:
        print(f"\n=== {label} (mode={mode}, min_score={min_score}) ===")
        recalls: list[float] = []
        rrs: list[float] = []
        total_violations = 0
        for item in queries:
            results = searcher.search(
                item["query"], limit=args.limit, min_score=min_score, mode=mode
            )
            names = [r.name for r in results]
            m = evaluate_query(names, item["expected"], item["must_not"])
            if m["recall"] is not None:
                recalls.append(m["recall"])
                rrs.append(m["rr"])
            total_violations += len(m["violations"])

            recall_txt = "-" if m["recall"] is None else f"{m['recall']:.2f}"
            missing = [e for e, r in m["ranks"].items() if r is None]
            print(f"- {item['query']!r}: recall@{args.limit}={recall_txt}, rr={m['rr']:.2f}")
            if missing:
                print(f"    thiếu: {missing}")
            if m["violations"]:
                print(f"    SAI (must_not lọt top): {m['violations']}")
            if args.verbose:
                for rank, name in enumerate(names, start=1):
                    print(f"    {rank}. {name}")

        summary[label] = {
            "recall": sum(recalls) / len(recalls) if recalls else 0.0,
            "mrr": sum(rrs) / len(rrs) if rrs else 0.0,
            "violations": float(total_violations),
        }

    print(f"\n=== TỔNG KẾT ({len(queries)} câu, top-{args.limit}) ===")
    print(f"{'cấu hình':<14}{'recall@k':>10}{'MRR':>8}{'must_not lọt':>14}")
    for label, s in summary.items():
        print(f"{label:<14}{s['recall']:>10.3f}{s['mrr']:>8.3f}{int(s['violations']):>14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())