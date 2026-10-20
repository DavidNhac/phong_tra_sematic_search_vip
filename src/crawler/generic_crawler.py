"""Generic crawler for blog-style articles (fallback for unknown parsers)."""
from typing import List, Dict, Any
import re
from src.crawler.base_crawler import BaseCrawler
import structlog

logger = structlog.get_logger()


class GenericCrawler(BaseCrawler):
    async def crawl(self, url: str) -> List[Dict[str, Any]]:
        logger.info(f"Starting generic crawl for {self.name}: {url}")
        
        html = await self.fetch_html(url)
        soup = self.parse_html(html)
        
        items = []
        headings = soup.find_all(["h2", "h3"])
        
        for idx, heading in enumerate(headings):
            text = self.extract_text(heading)
            
            if len(text) < 5 or len(text) > 100:
                continue
            if not re.search(r"\d+|cafe|lounge|phòng trà|coffee|bar|acoustic", text, re.IGNORECASE):
                continue
            
            # TÌM ẢNH
            next_heading = None
            for next_h in headings[idx + 1:]:
                if next_h.name == heading.name:
                    next_heading = next_h
                    break
            
            section_images = self.extract_section_images(heading, next_heading, url, max_images=3)
            primary_image = section_images[0] if section_images else ""
                
            content_parts = []
            current = heading.find_next_sibling()
            count = 0
            
            while current and current != next_heading and count < 8:
                if current.name in ["p", "div", "li", "ul"]:
                    content_parts.append(self.extract_text(current))
                current = current.find_next_sibling()
                count += 1
                
            full_text = " ".join(content_parts)
            
            items.append({
                "name": text,
                "image_url": primary_image,
                "image_urls": section_images,
                "address": self._extract_address_v2(full_text),  # Improved
                "price": self._extract_price_v2(full_text),      # Improved
                "hours": self._extract_hours_v2(full_text),      # Improved
                "description": full_text[:600],
                "raw_text": full_text
            })
            
        self.data = items
        logger.info(f"✅ Extracted {len(items)} items from {self.name}")
        
        items_with_images = sum(1 for item in items if item["image_url"])
        logger.info(f"📸 Found images for {items_with_images}/{len(items)} items")
        
        return items

    def _extract_address_v2(self, text: str) -> str:
        """Improved address extraction."""
        # Pattern 1: "Địa chỉ: ..." until "Giờ" or "Giá" or end of line
        match = re.search(
            r'(?:Địa chỉ|Address)[:\s]*([^\n]+?)(?=\s*(?:Giờ|Giá|Mức giá|Price|Opening)|$)',
            text, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()[:200]
        
        # Pattern 2: Vietnamese address pattern (number + street + district)
        match = re.search(
            r'(\d+[A-Za-z]?\s+(?:ngõ|ngách|phố|đường|tòa|tầng|tòa nhà)\s+[^\n,]+(?:,\s*[^\n,]+)*(?:,\s*Hà\s*Nội)?)',
            text, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()[:200]
        
        return ""

    def _extract_price_v2(self, text: str) -> str:
        """Improved price extraction."""
        # Pattern 1: "Giá: X - Y VNĐ"
        match = re.search(
            r'(?:Giá|Mức giá|Khoảng giá|Price)[:\s]*([^\n]+?)(?=\s*(?:Nếu|Hãy|Ghé|Đến)|$)',
            text, re.IGNORECASE
        )
        if match:
            price = match.group(1).strip()
            # Extract only price part
            price_match = re.search(r'(\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k)(?:\s*[-–to]\s*\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k))?)', price)
            if price_match:
                return price_match.group(0).strip()
            return price[:100]
        
        # Pattern 2: Direct price pattern
        match = re.search(
            r'(\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k)\s*(?:[-–to]\s*\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k))?)',
            text, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
        
        return ""

    def _extract_hours_v2(self, text: str) -> str:
        """Improved hours extraction."""
        # Pattern 1: "Giờ mở cửa: ..." or "Giờ phục vụ: ..."
        match = re.search(
            r'(?:Giờ\s*(?:phục vụ|mở cửa)|Opening hours)[:\s]*([^\n]+?)(?=\s*(?:Mức|Giá|Price|Nếu|Hãy)|$)',
            text, re.IGNORECASE
        )
        if match:
            hours = match.group(1).strip()
            # Extract only time pattern
            time_match = re.search(r'(\d{1,2}[hH:]\d{2}\s*(?:[-–to]\s*\d{1,2}[hH:]\d{2})?)', hours)
            if time_match:
                return time_match.group(0).strip()
            return hours[:100]
        
        # Pattern 2: Direct time pattern
        match = re.search(
            r'(\d{1,2}[hH:]\d{2}\s*(?:[-–to]\s*\d{1,2}[hH:]\d{2})?)',
            text
        )
        if match:
            return match.group(1).strip()
        
        # Pattern 3: "Mở cửa 24h"
        match = re.search(r'(Mở cửa 24h|Open 24 hours|24/7)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        return ""

    # Keep old methods for backward compatibility
    def _extract_address(self, text: str) -> str:
        return self._extract_address_v2(text)

    def _extract_price(self, text: str) -> str:
        return self._extract_price_v2(text)

    def _extract_hours(self, text: str) -> str:
        return self._extract_hours_v2(text)