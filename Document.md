Chạy lại từ bước crawl (Git Bash, đứng ở thư mục gốc repo)

rm -f data/raw/_.json # dọn file cũ dạng _\_timestamp.json
python -m src.crawler.pipeline configs/crawler_config.yaml
python -m src.processing.entity_resolver
python -m src.processing.deterministic_cleaner
python -m src.processing.llm_cleaner
python -m src.processing.llm_tagger

Sau bước crawl, mở data/raw/crawl_summary.json xem nguồn nào failed hoặc count bằng 0 và đọc thử raw_text vài mục trước khi chạy tiếp. Nếu nguồn nào lấy sai mục, chỉnh options: của nguồn đó (ví dụ heading_tags: [h3]).

Cơ chế loại trùng: đã có, nằm ở entity_resolver.py

Trùng giữa các nguồn: so tên đã bỏ dấu, bỏ số thứ tự và tiền tố “phòng trà/cafe/...”. Tên giống từ 85% thì gộp. Nếu hai bên có địa chỉ giống nhau thì chỉ cần tên giống từ 70%, nên “Trinh Ca” và “Trinh Ca Tea Room” vẫn gộp được.
Chặn gộp nhầm: hai mục khác số nhà hoặc khác tên phố sẽ không bao giờ gộp, nên các chi nhánh cùng tên vẫn tách riêng. Khi cân nhắc gộp, cặp điểm cao nhất được xét trước.
Sau khi gộp: mỗi trường lấy từ nguồn có priority cao nhất còn giá trị hợp lệ. Mô tả giống nhau từ 92% thì giữ một bản, nhưng vẫn ghi đủ các nguồn vào sources. Ảnh trùng URL bị loại, tên khác nhau được giữ trong aliases.
Trùng giữa các lần crawl: mỗi nguồn ghi đè vào một file cố định nên crawl lại không nhân đôi. Các file cũ dạng \*\_timestamp.json bị bỏ qua.

Giới hạn

Quán chỉ có tên và không có địa chỉ ở cả hai nguồn thì chỉ gộp khi tên giống từ 85%. Tên viết quá khác nhau (ví dụ tên tiếng Anh dịch hẳn) sẽ không gộp.
Một mục không có địa chỉ có thể gộp vào chi nhánh sai nếu tên giống hệt nhau.
mytour_1 và mytour_2 tính là hai nguồn riêng trong source_count, dù cùng một trang web.
Tôi chưa chạy trên dữ liệu thật của bạn. Sau lần chạy đầu, bạn xem các nhóm có source_count cao hoặc log “Large cluster” và “Merge refused” để kiểm tra gộp nhầm.

###SQL cấu trúc dữ liệu

## Cấu trúc dữ liệu: `data/processed/tagged_entities.json`

Mỗi quán trong file `tagged_entities.json` là một JSON object có các trường sau:

| Trường            | Kiểu dữ liệu    | Ghi chú / Mô tả                                                                                                                |
| :---------------- | :-------------- | :----------------------------------------------------------------------------------------------------------------------------- |
| `id`              | `string`        | Slug duy nhất, ví dụ: `"phong-tra-trinh-ca"`, dùng làm khóa chính                                                              |
| `name`            | `string`        | Tên đã bỏ số thứ tự và đuôi mô tả                                                                                              |
| `aliases`         | `list[string]`  | Các tên gọi khác của quán                                                                                                      |
| `address`         | `string`        | Địa chỉ chính, để `""` nếu không có                                                                                            |
| `district`        | `string`        | Quận/Huyện, để `""` nếu không xác định được (hiện 7/25 quán)                                                                   |
| `price`           | `string`        | Chuỗi hiển thị giá, dạng `"20.000 - 55.000 VNĐ"`, để `""` nếu không có                                                         |
| `price_min`       | `int` \| `null` | Giá tối thiểu (đơn vị: VNĐ), dùng để lọc theo giá                                                                              |
| `price_max`       | `int` \| `null` | Giá tối đa (đơn vị: VNĐ), dùng để lọc theo giá                                                                                 |
| `hours`           | `string`        | Giờ mở cửa, ví dụ: `"07:00 - 23:00"`, `"Mở cửa 24h"` hoặc `""`                                                                 |
| `description`     | `string`        | Mô tả tóm tắt từ nguồn có độ ưu tiên cao nhất                                                                                  |
| `descriptions`    | `list[object]`  | Mô tả chi tiết từ mọi nguồn. Mỗi mục gồm: `source`, `language`, `text`                                                         |
| `image_url`       | `string`        | URL ảnh đại diện, để `""` nếu không có                                                                                         |
| `image_urls`      | `list[string]`  | Danh sách URL ảnh bổ sung (tối đa 5 ảnh)                                                                                       |
| `languages`       | `list[string]`  | Ngôn ngữ bài viết/thông tin (ví dụ: `["vi"]`, `["en"]`)                                                                        |
| `sources`         | `list[object]`  | Danh sách nguồn thông tin. Mỗi mục gồm: `domain`, `source_url`, `raw_name`, `crawled_at`                                       |
| `source_count`    | `int`           | Số lượng nguồn riêng biệt có đề cập đến quán                                                                                   |
| `music_genres`    | `list[string]`  | Thể loại nhạc (ví dụ: `nhac_trinh`, `acoustic`, `jazz`, `bolero`, `lofi_chill`, `indie`, `traditional`, `piano`, `pop_ballad`) |
| `atmosphere_tags` | `list[string]`  | Nhãn không gian/bầu không khí (ví dụ: `yen_tinh`, `lang_man`, `co_kinh`, `view_dep`, `san_vuon`, `rooftop`, `sang              |

cấu trúc trong sql sever.
Vì sao tách bảng: một quán có nhiều tên gọi, ảnh, nguồn, tag và mô tả (quan hệ 1-nhiều). Nhét tất cả vào một cột thì không lọc được. Ví dụ muốn tìm quán có tag nhac_trinh thì chỉ cần WHERE tag_value = 'nhac_trinh' trên bảng tag. Đây cũng là phương án “bảng con” đã đề xuất ở handoff.

Cấu trúc:

tea_rooms (1 dòng/quán) ──┬── tea_room_aliases tên gọi khác
id (PK, số) ├── tea_room_descriptions mô tả theo từng nguồn
slug (unique, = id JSON) ├── tea_room_images ảnh (order_index 0 = ảnh chính)
name, address, district ├── tea_room_sources nguồn: tên, URL, raw_name, crawled_at
price, price_min/max └── tea_room_tags tag_type + tag_value
hours, description
image_url, source_count
address_alternatives, llm_filled (JSON)
created_at, updated_at

Mỗi bảng con có tea_room_id trỏ về tea_rooms.id. Xóa quán thì dòng con xóa theo (ON DELETE CASCADE). tea_room_tags.tag_type nhận 3 giá trị: music_genres, atmosphere_tags, features.

Nhận xét từ ảnh tea_room_aliases:

Bảng có 15 alias. Có vài alias là mảnh tiêu đề blog, không phải tên quán thật: “Thưởng trà và âm nhạc ở phòng trà Hà Nội Phố”, “Phòng trà Hà Nội nổi tiếng Ấy Lounge”, “Phòng trà ‘Hà Nội Phố’”. Đã ghi ở handoff là vô hại, nhưng nếu sau này dùng alias để tìm theo tên thì nên lọc.
Có alias trùng nghĩa chỉ khác dấu: “Acoustic Cafe” và “Acoustic café”.

Dữ liệu đã nằm trong SQL Server. Chạy lại lệnh lần hai phải ra created=0 updated=25 (bạn có thể kiểm tra). Bước tiếp theo: dựng văn bản embedding từ tea_rooms
