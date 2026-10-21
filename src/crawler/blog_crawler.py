"""BlogListCrawler: one config-driven crawler for "list of venues" blog posts.

Replaces MytourCrawler, DigiticketCrawler and GenericCrawler. It stores raw data only
(name, raw_text, images, source info). Address/price/hours are extracted later, in one
place (src/processing/field_extractors.py).
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Pattern

import structlog

from src.crawler.base_crawler import BaseCrawler

logger = structlog.get_logger()

NUMBERED = re.compile(r"^\s*\d+\s*[\.\)\-–:]\s*\S")
NUM_PREFIX = re.compile(r"^\s*\d+\s*[\.\)\-–:]\s*")
KEYWORD = re.compile(r"\b(?:cafe|café|coffee|lounge|bar|phòng trà|acoustic)\b", re.I)
EXCLUDE = re.compile(
    r"câu hỏi|kinh nghiệm|lưu ý|kết luận|lời kết|tổng kết|xem thêm|bảng giá|\btop\s*\d+\b",
    re.I,
)

LEAF_TAGS = ["p", "li", "h4", "h5", "h6", "blockquote", "figcaption", "tr", "dt", "dd"]
SKIP_TAGS = {"script", "style", "nav", "aside", "form", "noscript", "button", "iframe"}
NOISE_ATTR = re.compile(
    r"related|comment|share|social|sidebar|advert|breadcrumb|newsletter|subscribe", re.I
)
BR_MARK = "⁣"  # invisible separator used to keep <br> line breaks


class BlogListCrawler(BaseCrawler):
    def __init__(
        self,
        *args,
        heading_tags: Optional[List[str]] = None,
        heading_filter: str = "numbered",  # numbered | numbered_or_keyword | any
        name_pattern: Optional[str] = None,
        exclude_pattern: Optional[str] = None,
        min_heading_len: int = 5,
        max_heading_len: int = 120,
        max_section_chars: int = 4000,
        max_images: int = 3,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.heading_tags = [t.lower() for t in (heading_tags or ["h2"])]
        self.heading_filter = heading_filter
        self.name_pattern: Optional[Pattern] = re.compile(name_pattern) if name_pattern else None
        self.exclude_pattern: Pattern = re.compile(exclude_pattern, re.I) if exclude_pattern else EXCLUDE
        self.min_heading_len = min_heading_len
        self.max_heading_len = max_heading_len
        self.max_section_chars = max_section_chars
        self.max_images = max_images

    # ------------------------------------------------------------- heading logic
    def _accept_heading(self, text: str) -> bool:
        if not (self.min_heading_len <= len(text) <= self.max_heading_len):
            return False
        if self.name_pattern is not None:
            return bool(self.name_pattern.search(text))
        if self.heading_filter == "any":
            return not self.exclude_pattern.search(text)
        if NUMBERED.match(text):
            return not self.exclude_pattern.search(text)
        if self.heading_filter == "numbered_or_keyword":
            return bool(KEYWORD.search(text)) and not self.exclude_pattern.search(text)
        return False

    def _pick_headings(self, soup) -> List[Any]:
        """Use a single heading level per page. With several candidates, the level with the
        most numbered headings wins, so h3 children of an h2 are not treated as separate items."""
        best: List[Any] = []
        best_score = (-1, -1)
        for tag in self.heading_tags:
            accepted = [h for h in soup.find_all(tag) if self._accept_heading(self.extract_text(h))]
            score = (sum(1 for h in accepted if NUMBERED.match(self.extract_text(h))), len(accepted))
            if score > best_score:
                best, best_score = accepted, score
        return best

    def clean_heading(self, text: str) -> str:
        if self.name_pattern is not None:
            m = self.name_pattern.search(text)
            if m and "name" in m.groupdict() and m.group("name"):
                return m.group("name").strip()
        return NUM_PREFIX.sub("", text).strip(" .:-–")

    # -------------------------------------------------------------- section logic
    def _section_blocks(self, heading) -> List[Any]:
        """Sibling elements after the heading, up to the next heading of the same or higher level."""
        level = int(heading.name[1])
        stop = [f"h{i}" for i in range(1, level + 1)]

        node = heading
        # Heading wrapped in a container, e.g. <div class="title"><h2>...</h2></div>
        if (
            heading.find_next_sibling() is None
            and heading.parent is not None
            and heading.parent.name not in ("body", "html", "[document]")
        ):
            node = heading.parent

        blocks: List[Any] = []
        for sib in node.find_next_siblings():
            if sib.name in stop or sib.name == "footer" or sib.find(stop):
                break
            if sib.name in SKIP_TAGS or self._is_noise(sib):
                continue
            blocks.append(sib)
        return blocks

    @staticmethod
    def _is_noise(block) -> bool:
        attrs = " ".join(
            [" ".join(block.get("class", [])), block.get("id", "") or ""]
        )
        return bool(attrs.strip() and NOISE_ATTR.search(attrs))

    @staticmethod
    def _element_lines(el) -> List[str]:
        """Lines of one element: <br> becomes a line break, source-code newlines do not."""
        for br in el.find_all("br"):
            br.replace_with(BR_MARK)
        text = re.sub(r"\s+", " ", el.get_text(" "))
        return [p.strip() for p in text.split(BR_MARK) if p.strip()]

    def _block_lines(self, block) -> List[str]:
        if block.name in LEAF_TAGS:
            return self._element_lines(block)
        leaves = [el for el in block.find_all(LEAF_TAGS) if not el.find(LEAF_TAGS)]
        if not leaves:
            return self._element_lines(block)
        lines: List[str] = []
        for el in leaves:
            lines.extend(self._element_lines(el))
        return lines

    def _section_text(self, blocks: List[Any]) -> str:
        lines: List[str] = []
        total = 0
        for block in blocks:
            for line in self._block_lines(block):
                if lines and lines[-1] == line:
                    continue
                if total + len(line) > self.max_section_chars:
                    return "\n".join(lines)
                lines.append(line)
                total += len(line) + 1
        return "\n".join(lines)

    # --------------------------------------------------------------------- crawl
    async def crawl(self, url: str) -> List[Dict[str, Any]]:
        logger.info("Starting crawl", source=self.name, url=url)
        self.source_url = url
        html = await self.fetch_html(url)
        return self.parse_page(html, url)

    def parse_page(self, html: str, url: str) -> List[Dict[str, Any]]:
        """Parse an already-fetched page (also used by tests and for re-parsing saved HTML)."""
        self.source_url = url
        soup = self.parse_html(html)
        crawled_at = datetime.now().isoformat(timespec="seconds")

        items: List[Dict[str, Any]] = []
        for heading in self._pick_headings(soup):
            raw_name = self.extract_text(heading)
            name = self.clean_heading(raw_name)
            if not name:
                continue

            blocks = self._section_blocks(heading)
            raw_text = self._section_text(blocks)
            if not raw_text:
                logger.warning("Empty section", source=self.name, name=name)

            images = self.extract_section_images(blocks, url, self.max_images)
            items.append(
                {
                    "name": name,
                    "raw_name": raw_name,
                    "raw_text": raw_text,
                    "image_url": images[0] if images else "",
                    "image_urls": images,
                    "source": self.name,
                    "source_url": url,
                    "crawled_at": crawled_at,
                    "language": self.language,
                }
            )

        self.data = items
        with_images = sum(1 for i in items if i["image_url"])
        empty = sum(1 for i in items if not i["raw_text"])
        logger.info(
            "Extracted items",
            source=self.name,
            count=len(items),
            with_images=with_images,
            empty_text=empty,
        )
        return items
