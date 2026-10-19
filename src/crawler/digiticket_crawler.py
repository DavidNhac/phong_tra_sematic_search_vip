"""Crawler for digiticket.vn."""
from typing import List, Dict, Any
import re
from src.crawler.base_crawler import BaseCrawler
import structlog

logger = structlog.get_logger()

class DigiticketCrawler(BaseCrawler):
    # XÓA HOÀN TOÀN HÀM __init__ ở đây

    async def crawl(self, url: str) -> List[Dict[str, Any]]:
        logger.info(f"Starting digiticket crawl: {url}")
        html = await self.fetch_html(url)
        soup = self.parse_html(html)
        
        items = []
        headings = soup.find_all("h2")
        
        for heading in headings:
            text = self.extract_text(heading)
            if not re.search(r"\d+\.\s+.+", text):
                continue
                
            content_parts = []
            current = heading.find_next_sibling()
            count = 0
            
            while current and current.name != "h2" and count < 8:
                if current.name in ["p", "div", "li", "ul"]:
                    content_parts.append(self.extract_text(current))
                current = current.find_next_sibling()
                count += 1
                
            full_text = " ".join(content_parts)
            items.append({
                "name": text,
                "address": self._extract_address(full_text),
                "price": self._extract_price(full_text),
                "hours": self._extract_hours(full_text),
                "description": full_text[:600],
                "raw_text": full_text
            })
            
        self.data = items
        logger.info(f"✅ Extracted {len(items)} items from {self.name}")
        return items

    def _extract_address(self, text: str) -> str:
        match = re.search(r"Địa chỉ[:\s\-]+([^\n]+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _extract_price(self, text: str) -> str:
        match = re.search(r"Khoảng giá\s*(?:trung bình)?[:\s\-]+([^\n]+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _extract_hours(self, text: str) -> str:
        match = re.search(r"Giờ\s*mở\s*cửa[:\s\-]+([^\n]+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else ""