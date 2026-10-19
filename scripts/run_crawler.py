#!/usr/bin/env python3
"""CLI script to run the crawler pipeline."""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
import structlog

from src.crawler.pipeline import run_pipeline

app = typer.Typer(help="Crawler CLI")
logger = structlog.get_logger()


@app.command()
def crawl(
    config: str = typer.Option(
        "configs/crawler_config.yaml",
        help="Path to crawler config file"
    ),
):
    """Run the crawler pipeline."""
    logger.info("Starting crawler pipeline")
    
    try:
        results = asyncio.run(run_pipeline(config))
        
        # Print summary
        print("\n" + "=" * 60)
        print("CRAWLING SUMMARY")
        print("=" * 60)
        
        for result in results:
            status_icon = "✅" if result.get("status") == "success" else "❌"
            print(f"{status_icon} {result['source']}: {result.get('count', 0)} items")
        
        print("=" * 60)
        total = sum(r.get("count", 0) for r in results)
        print(f"Total items: {total}")
        print("=" * 60)
        
    except Exception as e:
        logger.error(f"Crawler failed: {e}")
        raise typer.Exit(code=1)


@app.command()
def test_source(
    source_name: str = typer.Argument(help="Source name to test"),
    config: str = typer.Option(
        "configs/crawler_config.yaml",
        help="Path to crawler config file"
    ),
):
    """Test crawling a single source."""
    import yaml
    
    with open(config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    
    source = next((s for s in cfg["sources"] if s["name"] == source_name), None)
    if not source:
        logger.error(f"Source '{source_name}' not found in config")
        raise typer.Exit(code=1)
    
    logger.info(f"Testing source: {source_name}")
    
    from src.crawler.pipeline import CrawlerPipeline
    pipeline = CrawlerPipeline(config)
    
    result = asyncio.run(pipeline.crawl_source(source))
    
    print("\n" + "=" * 60)
    print(f"TEST RESULT: {source_name}")
    print("=" * 60)
    print(f"Status: {result['status']}")
    print(f"Count: {result.get('count', 0)}")
    if result.get("error"):
        print(f"Error: {result['error']}")
    print("=" * 60)


if __name__ == "__main__":
    app()