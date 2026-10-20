"""Clean and normalize crawled data to extract structured fields."""
import json
import re
from pathlib import Path
from typing import Dict, Any
import structlog

logger = structlog.get_logger()


class DataCleaner:
    """Clean and extract structured data from raw crawled text."""
    
    def __init__(self, input_path: str = "data/processed/resolved_entities.json", 
                 output_path: str = "data/processed/cleaned_entities.json"):
        self.input_path = input_path
        self.output_path = output_path
        self.entities = []
    
    def load_data(self) -> int:
        """Load entities from JSON."""
        with open(self.input_path, 'r', encoding='utf-8') as f:
            self.entities = json.load(f)
        logger.info(f"Loaded {len(self.entities)} entities")
        return len(self.entities)
    
    def clean_address(self, text: str) -> str:
        """Extract only address, remove hours and price."""
        if not text:
            return ""
        
        # Remove "Giờ phục vụ", "Giờ mở cửa", "Opening hours"
        text = re.split(r'(?:Giờ\s*(?:phục vụ|mở cửa)|Opening hours)[:\s]*', text, flags=re.IGNORECASE)[0]
        
        # Remove "Mức giá", "Giá tham khảo", "Khoảng giá", "Price"
        text = re.split(r'(?:Mức\s+giá|Giá\s*(?:tham khảo)?|Khoảng\s+giá|Price)[:\s]*', text, flags=re.IGNORECASE)[0]
        
        # Remove "Nếu bạn đang tìm kiếm" và các câu marketing
        text = re.split(r'(?:Nếu bạn đang tìm kiếm|Hãy đến|Ghé thăm|Đến với)', text, flags=re.IGNORECASE)[0]
        
        # Clean up
        text = text.replace("Địa chỉ:", "").replace("Address:", "").strip()
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Remove trailing punctuation
        text = text.rstrip('.,;:')
        
        return text[:200]  # Limit length
    
    def clean_price(self, text: str) -> str:
        """Extract only price range."""
        if not text:
            return ""
        
        # Try to find price patterns
        patterns = [
            r'(\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k))\s*(?:[-–to]\s*(\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k)))?',
            r'(?:từ|from)\s*(\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k))',
            r'(\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k))\s*(?:[-–to]\s*(\d{1,3}(?:\.\d{3})?\s*(?:VNĐ|vnđ|đ|k)))',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(0).strip()
        
        # Fallback: extract text between "Giá" and next sentence
        match = re.search(r'(?:Giá|Mức giá|Khoảng giá|Price)[:\s]*([^\n.]+)', text, re.IGNORECASE)
        if match:
            price = match.group(1).strip()
            # Remove marketing text
            price = re.split(r'(?:Nếu bạn|Hãy đến|Ghé thăm)', price, flags=re.IGNORECASE)[0]
            return price[:100]
        
        return ""
    
    def clean_hours(self, text: str) -> str:
        """Extract only opening hours."""
        if not text:
            return ""
        
        # Try to find time patterns
        patterns = [
            r'(\d{1,2}[hH:]\d{2})\s*(?:[-–to]\s*(\d{1,2}[hH:]\d{2}))?',
            r'(\d{1,2}\s*(?:AM|PM|am|pm))\s*(?:[-–to]\s*(\d{1,2}\s*(?:AM|PM|am|pm)))?',
            r'(Mở cửa 24h|Open 24 hours|24/7)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0).strip()
        
        # Fallback: extract text between "Giờ" and next field
        match = re.search(r'(?:Giờ\s*(?:phục vụ|mở cửa)|Opening hours)[:\s]*([^\n.]+)', text, re.IGNORECASE)
        if match:
            hours = match.group(1).strip()
            # Remove price and marketing
            hours = re.split(r'(?:Mức giá|Giá|Nếu bạn|Hãy đến)', hours, flags=re.IGNORECASE)[0]
            return hours[:100]
        
        return ""
    
    def clean_description(self, text: str) -> str:
        """Remove address/price/hours from description."""
        if not text:
            return ""
        
        # Remove address line
        text = re.sub(r'Địa chỉ[:\s]+[^\n]+', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Address[:\s]+[^\n]+', '', text, flags=re.IGNORECASE)
        
        # Remove hours line
        text = re.sub(r'Giờ\s*(?:phục vụ|mở cửa)[:\s]+[^\n]+', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Opening hours[:\s]+[^\n]+', '', text, flags=re.IGNORECASE)
        
        # Remove price line
        text = re.sub(r'(?:Mức\s+)?giá\s*(?:tham khảo)?[:\s]+[^\n]+', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Price[:\s]+[^\n]+', '', text, flags=re.IGNORECASE)
        
        # Remove special characters and clean up
        text = text.replace('🞂', '-')  # Fix special character
        text = re.sub(r'[^\w\s,.;:!?()-]', '', text)  # Remove weird chars
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Remove marketing fluff
        text = re.sub(r'(?:Nếu bạn đang tìm kiếm|Hãy đến|Ghé thăm|Đến với)[^.]+', '', text, flags=re.IGNORECASE)
        
        return text[:800]
    
    def clean_all_entities(self) -> None:
        """Clean all entities."""
        logger.info("Starting data cleaning...")
        
        for i, entity in enumerate(self.entities):
            # Get raw text from description (contains all info mixed)
            raw_text = entity.get("description", "")
            
            # If address is empty or contains hours/price, extract from description
            if not entity.get("address") or "Giờ" in entity.get("address", "") or "Giá" in entity.get("address", ""):
                entity["address"] = self.clean_address(raw_text)
            
            # If price is empty or too long, extract from description
            if not entity.get("price") or len(entity.get("price", "")) > 50:
                entity["price"] = self.clean_price(raw_text)
            
            # If hours is empty, extract from description
            if not entity.get("hours"):
                entity["hours"] = self.clean_hours(raw_text)
            
            # Clean description to remove address/price/hours
            entity["description"] = self.clean_description(raw_text)
            
            # Clean name (remove numbers like "4. ")
            entity["name"] = re.sub(r'^\d+[\.\-\)]\s*', '', entity.get("name", "")).strip()
        
        logger.info(f"✅ Cleaned {len(self.entities)} entities")
    
    def save_cleaned_data(self) -> None:
        """Save cleaned entities to JSON."""
        with open(self.output_path, 'w', encoding='utf-8') as f:
            json.dump(self.entities, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ Saved cleaned data to {self.output_path}")
    
    def print_statistics(self) -> None:
        """Print cleaning statistics."""
        with_address = sum(1 for e in self.entities if e.get("address"))
        with_price = sum(1 for e in self.entities if e.get("price"))
        with_hours = sum(1 for e in self.entities if e.get("hours"))
        
        print("\n" + "=" * 60)
        print("DATA CLEANING STATISTICS")
        print("=" * 60)
        print(f"Total entities: {len(self.entities)}")
        print(f"With address: {with_address}/{len(self.entities)} ({with_address*100//len(self.entities)}%)")
        print(f"With price: {with_price}/{len(self.entities)} ({with_price*100//len(self.entities)}%)")
        print(f"With hours: {with_hours}/{len(self.entities)} ({with_hours*100//len(self.entities)}%)")
        print("=" * 60)


if __name__ == "__main__":
    cleaner = DataCleaner()
    cleaner.load_data()
    cleaner.clean_all_entities()
    cleaner.save_cleaned_data()
    cleaner.print_statistics()