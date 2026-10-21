"""Crawler pipeline: orchestrate all sources listed in configs/crawler_config.yaml."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import structlog
import yaml

from src.crawler.blog_crawler import BlogListCrawler

logger = structlog.get_logger()

# Old `parser:` values keep working. They are only presets for BlogListCrawler options;
# any key can be overridden per source under `options:`.
PARSER_PRESETS: Dict[str, Dict[str, Any]] = {
    "mytour": {"heading_tags": ["h2"], "heading_filter": "numbered"},
    "digiticket": {"heading_tags": ["h2"], "heading_filter": "numbered"},
    "blog": {"heading_tags": ["h2", "h3"], "heading_filter": "numbered_or_keyword"},
    "generic": {"heading_tags": ["h2", "h3"], "heading_filter": "numbered_or_keyword"},
}


class CrawlerPipeline:
    def __init__(self, config_path: str = "configs/crawler_config.yaml"):
        self.config = self._load_config(config_path)
        self.global_config: Dict[str, Any] = self.config.get("global", {}) or {}
        all_sources = self.config.get("sources", []) or []
        self.sources = [s for s in all_sources if s.get("enabled", True)]
        skipped = len(all_sources) - len(self.sources)
        logger.info("Loaded sources", enabled=len(self.sources), disabled=skipped)

    @staticmethod
    def _load_config(config_path: str) -> Dict[str, Any]:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @property
    def output_dir(self) -> str:
        return self.global_config.get("output_dir", "data/raw")

    def _get_crawler(self, source: Dict[str, Any]) -> BlogListCrawler:
        parser = source.get("parser", "generic")
        if parser not in PARSER_PRESETS:
            raise ValueError(f"Unknown parser: {parser}. Available: {list(PARSER_PRESETS)}")

        options = {**PARSER_PRESETS[parser], **(source.get("options") or {})}
        g = self.global_config
        return BlogListCrawler(
            name=source["name"],  # the source name, not the parser name
            output_dir=self.output_dir,
            timeout=g.get("timeout", 30),
            delay=g.get("delay_between_requests", 2.0),
            user_agent=g.get("user_agent", "PhongTraBot/1.0"),
            retry_attempts=g.get("retry_attempts", 2),
            fetch=source.get("fetch", "httpx"),
            save_html=g.get("save_html", True),
            html_dir=g.get("html_dir", "data/raw_html"),
            language=source.get("language", "auto"),
            priority=source.get("priority", 1),
            **options,
        )

    async def crawl_source(self, source: Dict[str, Any]) -> Dict[str, Any]:
        name, url = source["name"], source["url"]
        logger.info("Processing source", source=name)
        try:
            crawler = self._get_crawler(source)
            async with crawler:
                data = await crawler.crawl(url)
                output_path = crawler.save_data()  # <source>.json, overwrites the previous crawl
            return {
                "source": name,
                "url": url,
                "status": "success",
                "count": len(data),
                "output_file": str(output_path),
            }
        except Exception as e:
            logger.error("Failed to crawl", source=name, error=str(e))
            return {"source": name, "url": url, "status": "failed", "error": str(e), "count": 0}

    async def crawl_all(self) -> List[Dict[str, Any]]:
        concurrency = int(self.global_config.get("concurrency", 3))
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def run(source):
            async with semaphore:
                return await self.crawl_source(source)

        results = await asyncio.gather(*(run(s) for s in self.sources))
        ok = sum(1 for r in results if r["status"] == "success")
        logger.info(
            "Crawling complete",
            ok=ok,
            total=len(self.sources),
            items=sum(r["count"] for r in results),
        )
        return list(results)


async def run_pipeline(config_path: str = "configs/crawler_config.yaml") -> List[Dict[str, Any]]:
    pipeline = CrawlerPipeline(config_path)
    results = await pipeline.crawl_all()

    summary_path = Path(pipeline.output_dir) / "crawl_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "crawled_at": datetime.now().isoformat(timespec="seconds"),
        "total_sources": len(results),
        "successful": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "total_items": sum(r["count"] for r in results),
        "results": results,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Summary saved", path=str(summary_path))
    return results


if __name__ == "__main__":
    import sys

    asyncio.run(run_pipeline(sys.argv[1] if len(sys.argv) > 1 else "configs/crawler_config.yaml"))