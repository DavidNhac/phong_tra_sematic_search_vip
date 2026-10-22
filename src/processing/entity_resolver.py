"""Entity resolution: group the same venue across sources, then merge field by field.

Order: normalize each raw item -> match (name + address) -> merge per field by source priority.
Every source's description is kept (for tagging and embedding), nothing is dropped silently.
"""
import argparse
import glob
import json
import os
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

import structlog
from rapidfuzz import fuzz

from src.processing import field_extractors as fx

logger = structlog.get_logger()

_NAME_NOISE = re.compile(r"^(phòng trà|quán|cafe|café|coffee|lounge|bar)\s+", re.I)
_ADDR_FILLER = re.compile(
    r"\b(?:so|ngo|ngach|pho|duong|phuong|quan|to|toa nha|toa|tang|ha noi|hanoi|viet nam)\b"
)
_DISTRICTS_ASCII = [fx.strip_accents(d).lower() for d in fx.HANOI_DISTRICTS]


_JUNK_IMAGE = re.compile(r"booking-static|/tours/|ve-vao-cua|voucher|combo|/ads?/|banner|logo", re.I)
_NOT_VENUE = re.compile(
    r"^(?:the\s+)?(?:most|best|top)\b|\bcâu hỏi\b|\bhow to\b|\?\s*$|^(?:những|các|top)\s", re.I
)


def name_key(name: str) -> str:
    """Name used only for fuzzy matching (the displayed name keeps its own spelling)."""
    key = _NAME_NOISE.sub("", fx.clean_name(name)).lower()
    key = fx.strip_accents(key)  # "Cafe Giảng" and "Cafe Giang" are the same venue
    key = re.sub(r"\bca phe\b|\bcoffee\b|\bcafe\b", "cafe", key)
    key = re.sub(r"[^\w\s]", " ", key)
    key = re.sub(r"^(?:phong tra|quan|cafe|lounge|bar)\s+", "", re.sub(r"\s+", " ", key).strip())
    return key.strip()

_GENERIC_WORDS = re.compile(r"\b(?:phong tra|quan|cafe|ha noi|hanoi)\b")


def core_key(key: str) -> str:
    """Name key without generic words; used only when the addresses already match."""
    return re.sub(r"\s+", " ", _GENERIC_WORDS.sub(" ", key)).strip()

def _addr_parts(address: str) -> Tuple[str, Set[str]]:
    a = fx.strip_accents(address).lower()
    numbers = set(re.findall(r"\d+[a-z]?", a))
    for d in _DISTRICTS_ASCII:
        a = a.replace(d, " ")
    a = _ADDR_FILLER.sub(" ", re.sub(r"\d+[a-z]?(?:/\d+)*", " ", a))
    street = re.sub(r"[^a-z\s]", " ", a)
    return re.sub(r"\s+", " ", street).strip(), numbers


def address_relation(a: str, b: str) -> str:
    """'match', 'near' (same street, different house number: often a typo between blogs),
    'conflict' (different place) or 'unknown' (one side has no usable address)."""
    if not a or not b:
        return "unknown"
    street_a, nums_a = _addr_parts(a)
    street_b, nums_b = _addr_parts(b)
    both_streets = bool(street_a and street_b)
    street_score = fuzz.token_set_ratio(street_a, street_b) if both_streets else None

    if nums_a and nums_b:
        if nums_a & nums_b:  # same house/alley number
            if both_streets and street_score < 80:
                return "conflict"  # same number, other street
            return "match"
        if both_streets and street_score >= 90:
            return "near"
        return "conflict"
    if both_streets:
        return "match" if street_score >= 80 else "conflict"
    return "unknown"


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)  # lowest index = cluster root, stable


class EntityResolver:
    def __init__(
        self,
        name_threshold: float = 85.0,  # names only (an address is missing on one side)
        address_name_threshold: float = 70.0,  # names that differ in spelling but share an address
        include_legacy: bool = False,
    ):
        self.name_threshold = name_threshold
        self.address_name_threshold = address_name_threshold
        self.include_legacy = include_legacy
        self.items: List[Dict[str, Any]] = []
        self.resolved_entities: List[Dict[str, Any]] = []
        self.dropped: List[Dict[str, str]] = []

    # ------------------------------------------------------------------ loading
    def load_raw_data(self, data_dir: str = "data/raw") -> int:
        self.items, self.dropped = [], []
        for filepath in sorted(glob.glob(f"{data_dir}/*.json")):
            if os.path.basename(filepath) == "crawl_summary.json":
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error("Failed to load", path=filepath, error=str(e))
                continue

            if data.get("schema_version", 1) < 2 and not self.include_legacy:
                logger.warning(
                    "Skipping legacy raw file (re-crawl, or pass include_legacy)", path=filepath
                )
                continue

            meta = {
                "source": data.get("source", "unknown"),
                "source_url": data.get("source_url", ""),
                "language": data.get("language", ""),
                "priority": data.get("priority", 1),
                "crawled_at": data.get("crawled_at", ""),
            }
            for raw in data.get("data", []):
                item = self.normalize_item(raw, meta)
                if len(item["name"]) < 3 or _NOT_VENUE.search(item["name"]):
                    logger.warning("Dropped item (unusable name or not a venue)", raw_name=item["raw_name"],
                                   source=item["source"])
                    self.dropped.append({"raw_name": item["raw_name"], "source": item["source"]})
                    continue
                self.items.append(item)

        logger.info("Loaded raw items", kept=len(self.items), dropped=len(self.dropped))
        return len(self.items)

    @staticmethod
    def normalize_item(raw: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
        raw_name = unicodedata.normalize("NFC", raw.get("raw_name") or raw.get("name", ""))
        raw_text = unicodedata.normalize("NFC", raw.get("raw_text") or raw.get("description", "") or "")
        description = fx.build_description(raw_text)
        images = raw.get("image_urls") or ([raw["image_url"]] if raw.get("image_url") else [])
        language = raw.get("language") or meta["language"] or "auto"
        if language == "auto":  # not declared in config: guess from the text
            language = fx.detect_language(description)
        return {
            "name": fx.clean_name(raw.get("name") or raw_name),
            "raw_name": raw_name,
            "source": raw.get("source") or meta["source"],
            "source_url": raw.get("source_url") or meta["source_url"],
            "crawled_at": raw.get("crawled_at") or meta["crawled_at"],
            "priority": meta["priority"],
            "language": language,
            "address": fx.extract_address(raw_text),
            "price": fx.extract_price(raw_text),
            "hours": fx.extract_hours(raw_text),
            "description": description,
            "raw_text": raw_text,
            "images": [i for i in images
                       if isinstance(i, str) and i.startswith("http") and not _JUNK_IMAGE.search(i)],
        }

    # ----------------------------------------------------------------- matching
    def _match_score(self, a: Dict[str, Any], b: Dict[str, Any]) -> Optional[float]:
        """Score if the two items look like the same venue, else None."""
        relation = address_relation(a["address"], b["address"])
        if relation == "conflict":
            return None  # e.g. two branches of one chain
        sort_score = fuzz.token_sort_ratio(a["_key"], b["_key"])
        if relation == "near":  # same street, other number: only if the names are (almost) identical
            return sort_score if sort_score >= 95 else None
        if relation == "match":
            # same address: tolerate extra words ("Trinh Ca" vs "Trinh Ca Tea Room")
            score = max(sort_score, fuzz.token_set_ratio(a["_key"], b["_key"]))
            if len(a["_core"]) >= 3 and len(b["_core"]) >= 3:  # "g4u" vs "g4u ca nhac"
                score = max(score, fuzz.token_set_ratio(a["_core"], b["_core"]))
            return score if score >= self.address_name_threshold else None
        # relation == "unknown": one side has no usable address, so only the names can decide
        return sort_score if sort_score >= self.name_threshold else None

    def _compatible(self, group_a: List[int], group_b: List[int]) -> bool:
        """Two groups may merge only if no member of one conflicts with a member of the other.
        This stops an item without an address from chaining two different branches together."""
        return all(
            address_relation(self.items[i]["address"], self.items[j]["address"]) != "conflict"
            for i in group_a
            for j in group_b
        )

    def resolve(self) -> List[Dict[str, Any]]:
        logger.info("Resolving", items=len(self.items), name_threshold=self.name_threshold)
        self.resolved_entities = []
        
        for item in self.items:
            item["_key"] = name_key(item["name"])
            item["_core"] = core_key(item["_key"])

        pairs: List[Tuple[float, int, int]] = []
        for i in range(len(self.items)):
            for j in range(i + 1, len(self.items)):
                score = self._match_score(self.items[i], self.items[j])
                if score is not None:
                    pairs.append((score, i, j))
        pairs.sort(key=lambda p: (-p[0], p[1], p[2]))  # best matches first, deterministic

        uf = _UnionFind(len(self.items))
        groups: Dict[int, List[int]] = {i: [i] for i in range(len(self.items))}
        for _, i, j in pairs:
            ri, rj = uf.find(i), uf.find(j)
            if ri == rj:
                continue
            if not self._compatible(groups[ri], groups[rj]):
                logger.info("Merge refused (address conflict)",
                            a=self.items[i]["name"], b=self.items[j]["name"])
                continue
            uf.union(ri, rj)
            root = uf.find(ri)
            other = rj if root == ri else ri
            groups[root] = groups[root] + groups.pop(other)

        clusters: Dict[int, List[Dict[str, Any]]] = {}
        for idx, item in enumerate(self.items):
            clusters.setdefault(uf.find(idx), []).append(item)

        for members in clusters.values():
            if len(members) > 5:
                logger.warning("Large cluster, check for over-merging",
                               names=[m["name"] for m in members][:6], size=len(members))
            self.resolved_entities.append(self._merge_cluster(members))

        logger.info("Resolved", raw=len(self.items), unique=len(self.resolved_entities))
        return self.resolved_entities

    # ------------------------------------------------------------------ merging
    def _merge_cluster(self, cluster: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Highest priority first; ties keep file order, so the result is deterministic.
        ordered = sorted(enumerate(cluster), key=lambda p: (-p[1]["priority"], p[0]))
        items = [it for _, it in ordered]

        # Count occurrences of each name.
        counts = Counter(it["name"] for it in items if it["name"])

        # Choose canonical name:
        # most common -> Vietnamese diacritics -> shortest -> alphabetical.
        name = min(
            counts,
            key=lambda n: (
                -counts[n],
                fx.strip_accents(n) == n,
                len(n),
                n,
            ),
        )

        # Collect aliases, ignoring case differences and the canonical name.
        seen = {name.casefold()}
        aliases: List[str] = []

        for a in sorted(counts):
            if a.casefold() not in seen:
                seen.add(a.casefold())
                aliases.append(a)


        def first_valid(field: str, valid) -> str:
            for it in items:
                if it[field] and valid(it[field]):
                    return it[field]
            return ""

        address = first_valid("address", fx.is_valid_address)
        def vote(field: str) -> str:
            """Most common value across sources; ties go to the higher-priority source."""
            values = [it[field] for it in items if it[field]]
            if not values:
                return ""
            counts = Counter(values)
            best = max(counts.values())
            return next(v for v in values if counts[v] == best)

        price = vote("price")
        hours = vote("hours")
        
        # Only genuinely different addresses; spelling variants of the chosen one are not alternatives.
        address_alternatives = sorted(
            {it["address"] for it in items
             if it["address"] and fx.is_valid_address(it["address"]) and address
             and address_relation(address, it["address"]) in ("near", "conflict")}
        )

        descriptions: List[Dict[str, str]] = []
        for it in items:
            text = it["description"]
            if len(text) < 20:
                continue
            # Blogs copy each other: keep one of near-identical texts, but remember every source.
            if any(fuzz.ratio(text, d["text"]) >= 92 for d in descriptions):
                continue
            descriptions.append({"source": it["source"], "language": it["language"], "text": text})

        images: List[str] = []
        for it in items:
            for url in it["images"]:
                if url not in images:
                    images.append(url)

        sources = [
            {
                "domain": it["source"],
                "source_url": it["source_url"],
                "raw_name": it["raw_name"],
                "crawled_at": it["crawled_at"],
            }
            for it in items
        ]
        entity: Dict[str, Any] = {
            "name": name,
            "aliases": aliases,
            "address": address,
            "price": price,
            "hours": hours,
            "description": descriptions[0]["text"] if descriptions else "",
            "descriptions": descriptions,
            "raw_texts": [{"source": it["source"], "text": it["raw_text"]}
                          for it in items if it["raw_text"]],
            "image_url": images[0] if images else "",
            "image_urls": images[:5],
            "languages": sorted({it["language"] for it in items if it["language"]}),
            "sources": sources,
            "source_count": len({s["domain"] for s in sources}),
        }
        if address_alternatives:
            entity["address_alternatives"] = address_alternatives
        return entity

    # ------------------------------------------------------------------- saving
    def save_resolved_data(self, output_path: str = "data/processed/resolved_entities.json") -> None:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.resolved_entities, f, ensure_ascii=False, indent=2)
        logger.info("Saved resolved entities", count=len(self.resolved_entities), path=output_path)


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="data/raw")
    p.add_argument("--output", default="data/processed/resolved_entities.json")
    p.add_argument("--threshold", type=float, default=85.0)
    p.add_argument("--include-legacy", action="store_true")
    args = p.parse_args(argv)

    resolver = EntityResolver(name_threshold=args.threshold, include_legacy=args.include_legacy)
    if resolver.load_raw_data(args.data_dir) == 0:
        logger.warning("No raw data found. Run the crawler first.")
        return
    resolver.resolve()
    resolver.save_resolved_data(args.output)

    ents = resolver.resolved_entities
    print("\n" + "=" * 60)
    print("ENTITY RESOLUTION SUMMARY")
    print("=" * 60)
    print(f"Raw items: {len(resolver.items)} (dropped: {len(resolver.dropped)})")
    print(f"Unique entities: {len(ents)}")
    for label, key in (("images", "image_url"), ("address", "address"), ("hours", "hours"),
                       ("price", "price")):
        n = sum(1 for e in ents if e[key])
        print(f"With {label}: {n}/{len(ents)}")
    print("Top 5 by source count:")
    for e in sorted(ents, key=lambda x: x["source_count"], reverse=True)[:5]:
        print(f"  - {e['name']} ({e['source_count']} sources) aliases: {', '.join(e['aliases'][:3])}")
    print("=" * 60)


if __name__ == "__main__":
    main()