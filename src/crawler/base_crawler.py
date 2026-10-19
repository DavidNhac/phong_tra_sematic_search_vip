"""Base crawler with common functionality."""
import asyncio
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
import structlog
from playwright.async_api import async_playwright, Page, Browser

logger = structlog.get_logger()


class BaseCrawler:
    """Base class for all crawlers."""
    
    def __init__(
        self,
        name: str,
        output_dir: str = "data/raw",
        timeout: int = 30,
        delay: float = 2.0,
        user_agent: str = "PhongTraBot/1.0",
    ):
        self.name = name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.delay = delay
        self.user_agent = user_agent
        
        # Storage for crawled data
        self.data: List[Dict[str, Any]] = []
        
        # HTTP client
        self.client: Optional[httpx.AsyncClient] = None
        
        # Playwright
        self.playwright = None
        self.browser: Optional[Browser] = None
        
        logger.info(f"Initialized crawler: {name}")
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.client:
            await self.client.aclose()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
    
    async def fetch_html(self, url: str, use_playwright: bool = False) -> str:
        """Fetch HTML content from URL."""
        logger.info(f"Fetching: {url}")
        
        if use_playwright:
            return await self._fetch_with_playwright(url)
        else:
            return await self._fetch_with_httpx(url)
    
    async def _fetch_with_httpx(self, url: str) -> str:
        """Fetch using httpx (fast, for static pages)."""
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            await asyncio.sleep(self.delay)  # Rate limiting
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            raise
    
    async def _fetch_with_playwright(self, url: str) -> str:
        """Fetch using Playwright (for dynamic pages)."""
        if not self.browser:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True)
        
        page = await self.browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
            content = await page.content()
            await asyncio.sleep(self.delay)
            return content
        except Exception as e:
            logger.error(f"Playwright failed for {url}: {e}")
            raise
        finally:
            await page.close()
    
    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML with BeautifulSoup."""
        return BeautifulSoup(html, "lxml")
    
    def extract_text(self, element) -> str:
        """Extract clean text from BeautifulSoup element."""
        if element is None:
            return ""
        return element.get_text(strip=True)
    
    def save_data(self, filename: Optional[str] = None) -> Path:
        """Save crawled data to JSON file."""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.name}_{timestamp}.json"
        
        output_path = self.output_dir / filename
        
        data_to_save = {
            "source": self.name,
            "crawled_at": datetime.now().isoformat(),
            "count": len(self.data),
            "data": self.data,
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ Saved {len(self.data)} items to {output_path}")
        return output_path
    
    async def crawl(self, url: str) -> List[Dict[str, Any]]:
        """
        Main crawl method - must be implemented by subclasses.
        
        Args:
            url: URL to crawl
            
        Returns:
            List of extracted items
        """
        raise NotImplementedError("Subclasses must implement crawl()")