"""Generic crawler for blog-style articles (fallback for unknown parsers)."""
from typing import List, Dict, Any
import re
from src.crawler.base_crawler import BaseCrawler
import structlog

logger = structlog.get_logger()


class GenericCrawler(BaseCrawler):
    """Generic crawler that extracts h2/h3 headings and following paragraphs."""
    
    # Không cần __init__, sẽ dùng trực tiếp __init__ của BaseCrawler
    
    async def crawl(self, url: str) -> List[Dict[str, Any]]:
        logger.info(f"Starting generic crawl for {self.name}: {url}")
        
        html = await self.fetch_html(url)
        soup = self.parse_html(html)
        
        items = []
        # Tìm tất cả h2 và h3
        headings = soup.find_all(["h2", "h3"])
        
        for heading in headings:
            text = self.extract_text(heading)
            
            # Heuristic: Tên phải ngắn (< 100 ký tự) và chứa số hoặc từ khóa liên quan
            if len(text) < 5 or len(text) > 100:
                continue
            if not re.search(r"\d+|cafe|lounge|phòng trà|coffee|bar|acoustic", text, re.IGNORECASE):
                continue
                
            # Lấy 6 phần tử tiếp theo (p, div, li) để làm nội dung
            content_parts = []
            current = heading.find_next_sibling()
            count = 0
            
            while current and count < 6:
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
        match = re.search(r"(?:Địa chỉ|Address)[:\s\-]+([^\n]+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _extract_price(self, text: str) -> str:
        match = re.search(r"(?:Giá|Price|Khoảng giá|Giá tham khảo)[:\s\-]+([^\n]+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _extract_hours(self, text: str) -> str:
        match = re.search(r"(?:Giờ mở cửa|Opening hours)[:\s\-]+([^\n]+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else ""