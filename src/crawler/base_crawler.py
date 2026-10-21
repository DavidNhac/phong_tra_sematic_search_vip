"""Base crawler: fetch (httpx or Playwright) with retry, parsing helpers, raw output."""
import asyncio
import json
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import httpx
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger()

# Bumped when the raw JSON layout changes. The resolver refuses older files so that
# stale timestamped dumps (generic_2026....json) cannot be loaded twice.
SCHEMA_VERSION = 2

RETRY_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}

_BAD_IMAGE_EXT = (".svg", ".gif", ".ico")
_BAD_IMAGE_NAME = re.compile(
    r"(?:^|[-_.])(?:logo|avatar|banner|icon|sprite|placeholder|pixel|tracking|emoji|loading)(?:[-_.\d]|$)",
    re.I,
)


class BaseCrawler:
    """Base class for all crawlers. Subclasses implement crawl(url)."""

    def __init__(
        self,
        name: str,
        output_dir: str = "data/raw",
        timeout: int = 30,
        delay: float = 2.0,
        user_agent: str = "PhongTraBot/1.0",
        retry_attempts: int = 2,
        fetch: str = "httpx",
        save_html: bool = True,
        html_dir: str = "data/raw_html",
        language: str = "vi",
        priority: int = 1,
    ):
        self.name = name  # source name from config (NOT the parser name)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.delay = delay
        self.user_agent = user_agent
        self.retry_attempts = max(0, int(retry_attempts))
        self.use_playwright = fetch == "playwright"
        self.save_html = save_html
        self.html_dir = Path(html_dir)
        self.language = language
        self.priority = priority
        self.source_url = ""

        self.data: List[Dict[str, Any]] = []
        self.client: Optional[httpx.AsyncClient] = None
        self.playwright = None
        self.browser = None

        logger.info("Initialized crawler", source=name, fetch=fetch)

    async def __aenter__(self):
        self.client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.5",
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    # ------------------------------------------------------------------ fetching
    async def fetch_html(self, url: str, use_playwright: Optional[bool] = None) -> str:
        """Fetch HTML with retry + backoff on 403/429/5xx and network errors."""
        use_pw = self.use_playwright if use_playwright is None else use_playwright
        attempts = self.retry_attempts + 1
        last_error: Optional[Exception] = None

        for attempt in range(attempts):
            wait = min(2 ** attempt * 2, 30) + random.random()
            try:
                logger.info("Fetching", url=url, attempt=attempt + 1, playwright=use_pw)
                html = await (
                    self._fetch_with_playwright(url) if use_pw else self._fetch_with_httpx(url)
                )
                await asyncio.sleep(self.delay)
                self._save_raw_html(html)
                return html
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code not in RETRY_STATUS:
                    raise
                retry_after = e.response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = min(float(retry_after), 60)
                logger.warning("HTTP error", url=url, status=e.response.status_code)
            except Exception as e:  # network errors, Playwright timeouts
                last_error = e
                logger.warning("Fetch failed", url=url, error=str(e))

            if attempt < attempts - 1:
                await asyncio.sleep(wait)

        raise RuntimeError(f"Failed to fetch {url} after {attempts} attempts: {last_error}")

    async def _fetch_with_httpx(self, url: str) -> str:
        response = await self.client.get(url)
        response.raise_for_status()
        return response.text

    async def _fetch_with_playwright(self, url: str) -> str:
        from playwright.async_api import async_playwright  # imported lazily: optional dependency

        if not self.browser:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True)

        page = await self.browser.new_page(
            extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9"}
        )
        try:
            # networkidle often never fires on ad-heavy pages
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            await page.wait_for_timeout(1500)
            return await page.content()
        finally:
            await page.close()

    def _save_raw_html(self, html: str) -> None:
        if not self.save_html:
            return
        try:
            self.html_dir.mkdir(parents=True, exist_ok=True)
            (self.html_dir / f"{self.name}.html").write_text(html, encoding="utf-8")
        except OSError as e:
            logger.warning("Could not save raw html", source=self.name, error=str(e))

    # ------------------------------------------------------------------- parsing
    def parse_html(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def extract_text(self, element, sep: str = " ") -> str:
        """Text of one element with whitespace collapsed. Never glues words together."""
        if element is None:
            return ""
        return re.sub(r"\s+", " ", element.get_text(sep, strip=True)).strip()

    def get_absolute_url(self, base_url: str, src: str) -> str:
        if not src:
            return ""
        src = src.strip()
        if src.startswith(("data:", "javascript:")):
            return ""
        return urljoin(base_url, src)

    @staticmethod
    def _image_src(img) -> str:
        """Best candidate URL: lazy-load attributes first, then the largest srcset entry."""
        for attr in ("data-src", "data-lazy-src", "data-original"):
            if img.get(attr):
                return img.get(attr)
        srcset = img.get("srcset") or img.get("data-srcset") or ""
        if srcset:
            last = srcset.split(",")[-1].strip().split()
            if last:
                return last[0]
        return img.get("src") or ""

    @staticmethod
    def _is_good_image(url: str, img) -> bool:
        path = url.split("?")[0].lower()
        if path.endswith(_BAD_IMAGE_EXT):
            return False
        if _BAD_IMAGE_NAME.search(path.rsplit("/", 1)[-1]):
            return False
        if re.search(r"booking-static|/tours/|ve-vao-cua|voucher|combo", path):
            return False  # ticket / product widgets embedded in blog pages
        for attr in ("width", "data-width", "height", "data-height"):
            value = str(img.get(attr) or "")
            if value.isdigit() and int(value) < 100:
                return False
        return True

    def extract_section_images(
        self, blocks: Iterable[Any], base_url: str, max_images: int = 3
    ) -> List[str]:
        """Images found inside the given content blocks (already limited to one section)."""
        images: List[str] = []
        for block in blocks:
            for img in block.find_all("img"):
                url = self.get_absolute_url(base_url, self._image_src(img))
                if not url or url in images or not self._is_good_image(url, img):
                    continue
                images.append(url)
                if len(images) >= max_images:
                    return images
        return images

    # -------------------------------------------------------------------- saving
    def save_data(self, filename: Optional[str] = None) -> Path:
        """Save to <output_dir>/<source>.json. The fixed name overwrites the previous crawl."""
        output_path = self.output_dir / (filename or f"{self.name}.json")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "source": self.name,
            "source_url": self.source_url,
            "language": self.language,
            "priority": self.priority,
            "crawled_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(self.data),
            "data": self.data,
        }
        tmp = output_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(output_path)
        logger.info("Saved items", count=len(self.data), path=str(output_path))
        return output_path

    async def crawl(self, url: str) -> List[Dict[str, Any]]:
        raise NotImplementedError("Subclasses must implement crawl()")