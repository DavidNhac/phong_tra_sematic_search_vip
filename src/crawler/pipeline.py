"""Crawler pipeline - orchestrate all crawlers."""
import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
import yaml

import structlog

# === QUAN TRỌNG: Đảm bảo các import này có đầy đủ ===
from src.crawler.base_crawler import BaseCrawler
from src.crawler.mytour_crawler import MytourCrawler
from src.crawler.digiticket_crawler import DigiticketCrawler
from src.crawler.generic_crawler import GenericCrawler

logger = structlog.get_logger()

# Registry of crawlers
CRAWLER_REGISTRY = {
    "mytour": MytourCrawler,
    "digiticket": DigiticketCrawler,
    "generic": GenericCrawler,  # <-- Đã thêm generic vào đây
}


class CrawlerPipeline:
    """Orchestrate crawling from multiple sources."""
    
    def __init__(self, config_path: str = "configs/crawler_config.yaml"):
        self.config = self._load_config(config_path)
        self.global_config = self.config.get("global", {})
        self.sources = self.config.get("sources", [])
        
        logger.info(f"Loaded {len(self.sources)} sources from config")
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load YAML config."""
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    
    
    def _get_crawler(self, parser_name: str) -> BaseCrawler:
        """Get crawler instance by parser name."""
        crawler_class = CRAWLER_REGISTRY.get(parser_name)
        if not crawler_class:
            raise ValueError(f"Unknown parser: {parser_name}. Available: {list(CRAWLER_REGISTRY.keys())}")
        
        return crawler_class(
            name=parser_name,  # <-- THÊM DÒNG NÀY
            output_dir=self.global_config.get("output_dir", "data/raw"),
            timeout=self.global_config.get("timeout", 30),
            delay=self.global_config.get("delay_between_requests", 2.0),
            user_agent=self.global_config.get("user_agent", "PhongTraBot/1.0"),
        )
    
    async def crawl_source(self, source: Dict[str, Any]) -> Dict[str, Any]:
        """Crawl a single source."""
        name = source["name"]
        url = source["url"]
        parser = source["parser"]
        
        logger.info(f"Processing source: {name}")
        
        try:
            crawler = self._get_crawler(parser)
            
            async with crawler:
                data = await crawler.crawl(url)
                output_path = crawler.save_data()
            
            return {
                "source": name,
                "url": url,
                "status": "success",
                "count": len(data),
                "output_file": str(output_path),
            }
        
        except Exception as e:
            logger.error(f"Failed to crawl {name}: {e}")
            return {
                "source": name,
                "url": url,
                "status": "failed",
                "error": str(e),
                "count": 0,
            }
    
    async def crawl_all(self, concurrency: int = 3) -> List[Dict[str, Any]]:
        """Crawl all sources with concurrency control."""
        semaphore = asyncio.Semaphore(concurrency)
        
        async def crawl_with_semaphore(source):
            async with semaphore:
                return await self.crawl_source(source)
        
        tasks = [crawl_with_semaphore(source) for source in self.sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log summary
        success_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success")
        total_items = sum(r.get("count", 0) for r in results if isinstance(r, dict))
        
        logger.info(f"✅ Crawling complete: {success_count}/{len(self.sources)} sources, {total_items} items total")
        
        return results


async def run_pipeline(config_path: str = "configs/crawler_config.yaml"):
    """Run the crawler pipeline."""
    pipeline = CrawlerPipeline(config_path)
    results = await pipeline.crawl_all()
    
    # Save summary
    summary_path = Path("data/raw/crawl_summary.json")
    
    summary = {
        "crawled_at": datetime.now().isoformat(),
        "total_sources": len(results),
        "successful": sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success"),
        "failed": sum(1 for r in results if isinstance(r, dict) and r.get("status") == "failed"),
        "total_items": sum(r.get("count", 0) for r in results if isinstance(r, dict)),
        "results": results,
    }
    
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Summary saved to {summary_path}")
    return results