# 🍵 Phòng Trà Hà Nội - Semantic Search

Hệ thống tìm kiếm ngữ nghĩa cho các phòng trà tại Hà Nội, sử dụng Qdrant và BGE-M3.

## ✨ Tính năng

- 🕷️ Crawler tự động từ 10+ nguồn (Mytour, Digiticket, Vinwonders...)
- 🧩 Entity Resolution: Gộp quán trùng tên từ nhiều nguồn
- 🎯 Hybrid Search: Dense + Sparse + RRF Fusion
- 🏷️ Auto Tagging: Rule-based + LLM
- 🔍 Metadata Filtering: Quận, giá, thể loại nhạc
- 🚀 FastAPI với OpenAPI docs

## 🚀 Quick Start

### Yêu cầu

- Python 3.11+
- Docker & Docker Compose
- [uv](https://docs.astral.sh/uv/) (package manager)

### Cài đặt

```bash
# Clone repo
git clone https://github.com/davidnhac/phong-tra-semantic-search.git
cd phong-tra-semantic-search

# Copy env
cp .env.example .env

# Setup
make dev

# Khởi động Qdrant
make docker-up

# Crawl data
make crawl

# Index vào Qdrant
make index

# Chạy API
make serve
```
