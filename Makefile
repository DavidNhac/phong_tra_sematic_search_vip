.PHONY: help install dev test crawl index serve docker-up docker-down clean

help:  ## Hiển thị help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Cài đặt dependencies production
	uv sync

dev:  ## Cài đặt dependencies dev
	uv sync --extra dev

test:  ## Chạy tests
	uv run pytest

crawl:  ## Chạy crawler
	uv run python scripts/run_crawler.py

index:  ## Index data vào Qdrant
	uv run python scripts/run_indexer.py

setup-db:  ## Setup Qdrant collection
	uv run python src/database/indexer.py

serve:  ## Chạy API server
	uv run uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

docker-up:  ## Khởi động Docker services
	docker compose up -d

docker-down:  ## Tắt Docker services
	docker compose down

docker-logs:  ## Xem logs
	docker compose logs -f

clean:  ## Dọn dẹp cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

test-connection:  ## Test kết nối Qdrant
	uv run python src/database/qdrant_client.py
