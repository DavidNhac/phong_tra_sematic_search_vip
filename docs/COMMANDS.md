# COMMANDS: lệnh theo từng giai đoạn (crawl → search)

Chạy trong **Git Bash, thư mục gốc repo** `D:\phong_tra_semantic_vip\phong-tra-semantic-search`, mọi lệnh dạng `python -m src.<module>`.

## 0. Chuẩn bị môi trường

```bash
source .venv/Scripts/activate        # kích hoạt đúng venv của repo (nếu terminal còn venv cũ: deactivate trước)
uv sync                              # cài/đồng bộ thư viện theo pyproject.toml
uv add <goi>                         # thêm thư viện;  uv add pytest --dev  cho thư viện dev
python -m pytest tests/unit -v       # chạy toàn bộ test
```

Điều kiện: file `.env` có `GEMINI_API_KEY` và `SQL_SERVER_URL` (dạng pyodbc, Windows Auth):
`mssql+pyodbc://@localhost/phong_tra_db?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes&TrustServerCertificate=yes`

## 1. Crawl (cào dữ liệu thô)

```bash
rm -f data/raw/*.json                                          # chỉ khi muốn cào lại từ đầu
python -m src.crawler.pipeline configs/crawler_config.yaml
```

Ra: `data/raw/<nguồn>.json`, `data/raw_html/<nguồn>.html`, `crawl_summary.json`. Nguồn lỗi (`wecheckin`, `vinpearl`) nên comment trong `configs/crawler_config.yaml`.

## 2. Gộp trùng

```bash
python -m src.processing.entity_resolver
```

Ra: `data/processed/resolved_entities.json` (65 mục → 25 quán).

## 3. Làm sạch

```bash
python -m src.processing.deterministic_cleaner     # regex: địa chỉ, giá, giờ, quận
python -m src.processing.llm_cleaner               # Gemini điền chỗ trống (có cache, giới hạn RPM)
```

Ra: `cleaned_entities.json`, `llm_cleaned_entities.json`.

## 4. Gắn tag

```bash
python -m src.processing.llm_tagger                # dùng cache nên chạy lại không tốn request; xem dòng "Removed N tags"
```

Ra: `data/processed/tagged_entities.json` (dữ liệu sẵn sàng nạp).

## 5. Nạp SQL Server

```bash
python -m src.database.load_sql --create-tables    # lần đầu: tạo 6 bảng rồi nạp (upsert theo slug)
python -m src.database.load_sql                    # nạp lại, chạy nhiều lần không nhân đôi
python -m src.database.load_sql --prune            # nạp và xóa quán không còn trong file
python -m src.database.load_sql --recreate         # CẨN THẬN: xóa toàn bộ bảng rồi tạo lại (khi đổi schema)
```

Kiểm tra trong SSMS:

```sql
SELECT (SELECT COUNT(*) FROM tea_rooms) AS quan, (SELECT COUNT(*) FROM tea_room_descriptions) AS mo_ta;  -- 25 và 65
```

## 6. Qdrant

```bash
curl http://localhost:6333/healthz                 # "healthz check passed"
curl http://localhost:6333/collections
python -m src.database.client                      # test kết nối Qdrant
python -m src.database.indexer --recreate          # (tùy chọn) tạo collection rỗng + index; build_index tự làm bước này
docker ps                                          # container phong-tra-qdrant (trạng thái "unhealthy" có thể bỏ qua nếu healthz pass)
```

Dashboard: http://localhost:6333/dashboard

## 7. Embedding

```bash
python -m src.embeddings.build_index --dry-run     # xem số điểm, số token, 2 văn bản mẫu; không ghi gì
python -m src.embeddings.build_index               # embed và DỰNG LẠI collection (65 điểm)
python -m src.embeddings.build_index --no-tags     # embed không kèm tag (để so sánh)
export HF_HOME=/d/hf_cache                         # (tùy chọn, trước lệnh trên) đổi nơi lưu model khỏi ổ C
```

Chạy lại `build_index` **sau mỗi lần chạy `load_sql`** (id mô tả đổi mỗi lần nạp).

## 8. Search

```bash
python -m src.search.searcher                                       # chế độ hỏi liên tục (Enter trống để thoát)
python -m src.search.searcher "quán nhạc Trịnh yên tĩnh"            # một câu hỏi
python -m src.search.searcher "cafe acoustic" --limit 10
python -m src.search.searcher "cafe acoustic" --district "Cầu Giấy" --max-price 100000 --tag acoustic
python -m src.search.searcher "nhạc sống" --tag co_live_music --tag yen_tinh --min-score 0.5
```

Tham số: `--limit`, `--district`, `--max-price` (giá thấp nhất của quán ≤ giá trị; loại quán thiếu giá), `--tag` (lặp lại nhiều lần, AND), `--min-score`.

Tag hợp lệ:

- `music_genres`: `nhac_trinh acoustic jazz bolero lofi_chill indie traditional piano pop_ballad`
- `atmosphere_tags`: `yen_tinh lang_man co_kinh view_dep san_vuon rooftop sang_trong binh_dan tre_trung co_dien`
- `features`: `co_live_music cho_dat_cho do_an_nhe parking khong_thuoc_la wifi_manh`

## 9. Chạy lại toàn bộ (dữ liệu thô không đổi)

```bash
python -m src.processing.entity_resolver
python -m src.processing.deterministic_cleaner
python -m src.processing.llm_cleaner
python -m src.processing.llm_tagger
python -m src.database.load_sql --create-tables
python -m src.embeddings.build_index
python -m src.search.searcher
```

Nếu cào lại dữ liệu thô: thêm `rm -f data/raw/*.json` và `python -m src.crawler.pipeline configs/crawler_config.yaml` lên đầu.

## 10. Lỗi hay gặp

| Hiện tượng                                         | Cách xử lý                                                                                                     |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `No module named pytest` / venv sai thư mục        | `deactivate`, rồi `source .venv/Scripts/activate`; hoặc `uv run python -m ...`                                 |
| `unexpected keyword argument 'trusted_connection'` | `pymssql` không hỗ trợ; dùng URL `mssql+pyodbc` ở mục 0                                                        |
| `Could not parse SQLAlchemy URL`                   | `SQL_SERVER_URL` viết sai: không khoảng trắng (dùng `+`), mã hóa ký tự đặc biệt trong mật khẩu, không dấu nháy |
| `Qdrant client version ... incompatible`           | `uv add "qdrant-client>=1.12,<1.13"`                                                                           |
| `UnicodeEncodeError` ở console                     | `PYTHONUTF8=1 python -m ...`                                                                                   |

## rút gọn luồng chạy

Tất cả chạy từ thư mục gốc repo, trong Git Bash.

**0. Chuẩn bị môi trường**

```bash
source .venv/Scripts/activate
export PYTHONUTF8=1                      # tránh lỗi tiếng Việt khi chuyển hướng output
docker start phong-tra-qdrant            # container Qdrant đã có sẵn
curl http://localhost:6333/healthz       # phải in: healthz check passed
python -m src.database.client            # kiểm tra kết nối Qdrant
```

**1. Thu thập dữ liệu**

```bash
python -m src.crawler.pipeline           # ghi data/raw/*.json và data/raw_html/
```

**2. Hợp nhất thực thể** (64 mục thô thành 24 quán)

```bash
python -m src.processing.entity_resolver
```

**3. Làm sạch**

```bash
python -m src.processing.deterministic_cleaner
python -m src.processing.llm_cleaner     # gọi Gemini, cần GEMINI_API_KEY
```

**4. Gán nhãn**

```bash
python -m src.processing.llm_tagger
python -m src.processing.llm_tagger --show-dropped      # xem nhãn bị loại vì thiếu bằng chứng
python -m src.processing.llm_tagger --explain "Xóm"     # xem bằng chứng của từng nhãn
```

**5. Nạp SQL Server**

```bash
python -m src.database.load_sql --create-tables --prune
```

Kết quả mong đợi khi dữ liệu không đổi: `created 0, updated 24, deleted 0`.

**6. Dựng index Qdrant** (bắt buộc chạy lại sau mỗi lần bước 5)

```bash
python -m src.embeddings.build_index --dry-run   # chỉ xem văn bản và số token
python -m src.embeddings.build_index             # xóa và dựng lại collection
```

Kết quả mong đợi: `64 điểm từ 24 quán`.

**7. Tìm kiếm**

```bash
python -m src.search.searcher "quán nhạc Trịnh yên tĩnh"
python -m src.search.searcher "cafe acoustic" --district "Cầu Giấy" --max-price 100000 --tag acoustic
python -m src.search.searcher "quán nhạc bolero" --limit 5 --min-score 0   # 0 = xem hết, không cắt ngưỡng
python -m src.search.searcher                                              # chế độ hỏi liên tục, chỉ tải model một lần
```

**8. Kiểm thử**

```bash
python -m pytest tests/unit -v           # hiện 18 test pass
```

Ghi chú:

- Chạy lại từ bước nào thì phải chạy tiếp mọi bước sau nó. Riêng bước 4 không gọi lại Gemini nếu cache còn (đổi luật lọc bằng chứng thì chạy lại là đủ).
- Chỉ đổi mô tả hoặc nhãn thì bắt đầu từ bước 3; chỉ đổi `embedder.py` thì chỉ cần bước 6.
- Mình chưa xem `pipeline.py` nên không biết nó có tham số dòng lệnh nào; lệnh ở bước 1 là dạng bạn đã chạy thành công.
