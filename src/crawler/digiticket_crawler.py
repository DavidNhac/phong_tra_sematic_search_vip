"""Crawler for digiticket.vn."""
from typing import List, Dict, Any
import re
from src.crawler.base_crawler import BaseCrawler
import structlog

logger = structlog.get_logger()


class DigiticketCrawler(BaseCrawler):
    async def crawl(self, url: str) -> List[Dict[str, Any]]:
        logger.info(f"Starting digiticket crawl: {url}")
        html = await self.fetch_html(url)
        soup = self.parse_html(html)
        
        items = []
        headings = soup.find_all("h2")
        
        for idx, heading in enumerate(headings):
            text = self.extract_text(heading)
            if not re.search(r"\d+\.\s+.+", text):
                continue
            
            next_heading = headings[idx + 1] if idx + 1 < len(headings) else None
            section_images = self.extract_section_images(heading, next_heading, url, max_images=3)
            primary_image = section_images[0] if section_images else ""
                
            content_parts = []
            current = heading.find_next_sibling()
            count = 0
            
            while current and current != next_heading and count < 15:
                if current.name in ["p", "div", "li", "ul"]:
                    content_parts.append(self.extract_text(current))
                current = current.find_next_sibling()
                count += 1
                
            full_text = " ".join(content_parts)
            
            items.append({
                "name": text,
                "image_url": primary_image,
                "image_urls": section_images,
                "address": self._extract_address(full_text),
                "price": self._extract_price(full_text),
                "hours": self._extract_hours(full_text),
                "description": full_text[:600],
                "raw_text": full_text
            })
            
        self.data = items
        logger.info(f"✅ Extracted {len(items)} items from {self.name}")
        
        items_with_images = sum(1 for item in items if item["image_url"])
        logger.info(f"📸 Found images for {items_with_images}/{len(items)} items")
        
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