"""Shared, conservative extractors for address / price / hours / description.

Rules that fix the old regex bugs:
  * labels are matched at the start of a line (raw_text now keeps line breaks), never mid-word
    ("đánh giá" no longer looks like the label "giá");
  * keywords are case-sensitive or must be followed by a digit ("một số quán" is not an address);
  * values are validated and capped, and "" is returned instead of a guess;
  * ranges use a real separator list ("-", "–", "đến", "to"), not the character class [-–to];
  * "50k", "50 nghìn", "1,5tr" and "20.000đ - 55.000đ" are all understood;
  * nothing here ever truncates descriptions.
"""
import re
import unicodedata
from typing import List, Optional, Tuple

HANOI_DISTRICTS = [
    "Hoàn Kiếm", "Ba Đình", "Đống Đa", "Hai Bà Trưng", "Tây Hồ", "Cầu Giấy",
    "Thanh Xuân", "Hoàng Mai", "Long Biên", "Nam Từ Liêm", "Bắc Từ Liêm", "Hà Đông",
]
_DISTRICT_ALT = "|".join(HANOI_DISTRICTS)
_BULLETS = r"[\s•·\-–—*▪■◆➤>🞂]*"

# --------------------------------------------------------------------- utilities
def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D")


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", strip_accents(s).lower())
    return s.strip("-")


def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s.strip(" .,;:-–—_*\"'")


_NAME_PREFIX = re.compile(r"^\s*\d+\s*[\.\)\-–:]\s*")


_NAME_SPLIT = re.compile(r"\s+[-–—|]\s+|\s*:\s+")
_NAME_DESCRIPTIVE = re.compile(
    r"phòng trà|điểm đến|điểm hẹn|nổi tiếng|hà nội|ca sĩ|yêu |thu hút|độc đáo|một trong|"
    r"những|dành cho|cho người|cho những|sang, xịn|tuyệt vời|không gian|địa chỉ|đêm nhạc",
    re.I,
)
_NAME_LEAD = re.compile(r"^(?:nghe|thưởng thức|trải nghiệm)\b[^,]{0,45}?\s(?:ở|tại)\s+", re.I)


def clean_name(name: str) -> str:
    """Venue name without list numbering and without descriptive heading tails.

    "Cafe Cuối Ngõ – Phòng trà nhạc Trịnh Hà Nội cho tâm hồn hoài niệm" -> "Cafe Cuối Ngõ"
    "Zone Music – Cafe & Lounge" is kept (the tail is part of the name).
    """
    name = unicodedata.normalize("NFC", name or "")
    name = _NAME_PREFIX.sub("", name)
    name = _NAME_LEAD.sub("", name)
    parts = _NAME_SPLIT.split(name, maxsplit=1)
    if len(parts) == 2 and len(parts[0].strip()) >= 3:
        tail = parts[1]
        if _NAME_DESCRIPTIVE.search(tail) or len(tail) > 35:
            name = parts[0]
    return _tidy(name)


_VI_CHARS = set("ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ")


def detect_language(text: str) -> str:
    """Cheap vi/en guess from Vietnamese-only letters. Good enough to tag mixed sources."""
    letters = [c for c in (text or "").lower() if c.isalpha()]
    if len(letters) < 20:
        return "unknown"
    vi = sum(1 for c in letters if c in _VI_CHARS)
    return "vi" if vi / len(letters) > 0.03 else "en"


# ------------------------------------------------------------------------ address
_ADDR_LABEL = re.compile(rf"(?m)^{_BULLETS}(?i:địa chỉ|address)\s*[:：]\s*(?P<v>[^\n]+)")
_FIELD_STOP = re.compile(
    r"\s+(?=(?:Giờ\s*(?:mở cửa|phục vụ|hoạt động)|Mức giá|Khoảng giá|Giá tham khảo|Giá vé|Giá"
    r"|Opening hours|Price|Hotline|Liên hệ|Website|Fanpage|Điện thoại|SĐT)\b)"
)
_ADDR_IMPLICIT = re.compile(
    r"(?<!\w)(?:(?i:số)\s+\d+|\d+[A-Za-z]?(?:/\d+)*\s+(?i:ngõ|ngách|phố|đường|p\.|đ\.)"
    r"|(?i:ngõ|ngách)\s+\d+|(?:Tòa|Tầng)\s+\w+)[^\n]*"
)
_ADDR_END_CITY = re.compile(r"^.{5,120}?(?:Hà Nội|Hanoi)(?:\s*,?\s*Việt Nam)?", re.I)
_ADDR_END_DISTRICT = re.compile(rf"^.{{5,120}}?(?:{_DISTRICT_ALT})", re.I)
_ADDR_BAD = re.compile(r"nếu bạn|hãy |ghé |đến với|bạn có thể|tại đây", re.I)
_ADDR_HINT = re.compile(
    rf"\d|hà nội|hanoi|{_DISTRICT_ALT}|\b(?:phố|đường|ngõ|ngách|phường|quận)\b", re.I
)


def is_valid_address(a: str) -> bool:
    a = (a or "").strip()
    if not (6 <= len(a) <= 150) or "\n" in a or len(a.split()) > 25:
        return False
    return bool(_ADDR_HINT.search(a)) and not _ADDR_BAD.search(a)


def extract_address(text: str) -> str:
    if not text:
        return ""
    m = _ADDR_LABEL.search(text)
    if m:
        value = _tidy(_FIELD_STOP.split(m.group("v"), maxsplit=1)[0])
        if is_valid_address(value):
            return value[:150]

    for line in text.split("\n"):
        m = _ADDR_IMPLICIT.search(line)
        if not m:
            continue
        cand = m.group(0)
        end = _ADDR_END_CITY.match(cand) or _ADDR_END_DISTRICT.match(cand)
        if end:
            value = _tidy(end.group(0))
            if is_valid_address(value) and len(value) <= 120:
                return value
    return ""


def find_district(address: str) -> str:
    m = re.search(_DISTRICT_ALT, address or "", re.I)
    if not m:
        return ""
    wanted = strip_accents(m.group(0)).lower()
    for d in HANOI_DISTRICTS:
        if strip_accents(d).lower() == wanted:
            return d
    return ""


# -------------------------------------------------------------------------- price
_NUM = r"\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?"
_NOT_LETTER = r"(?![^\W\d_])"
_UNIT_K = rf"(?:k|nghìn|ngàn){_NOT_LETTER}"
_UNIT_M = rf"(?:triệu|tr){_NOT_LETTER}"
_UNIT_D = rf"(?:vnđ|vnd|đ|₫){_NOT_LETTER}"
_UNIT = rf"(?:{_UNIT_K}|{_UNIT_M}|{_UNIT_D})"
_SEP = r"(?:-|–|—|đến|tới|to)"
_PRICE_RANGE = re.compile(
    rf"(?<![\d.,])(?P<a>{_NUM})\s*(?P<ua>{_UNIT})?\s*{_SEP}\s*(?P<b>{_NUM})\s*(?P<ub>{_UNIT})", re.I
)
_PRICE_ONE = re.compile(rf"(?<![\d.,])(?P<a>{_NUM})\s*(?P<u>{_UNIT})", re.I)
_PRICE_LABEL = re.compile(
    rf"(?m)^{_BULLETS}(?i:mức giá|khoảng giá|giá tham khảo|giá vé|giá)"
    r"\s*(?:trung bình|dự kiến)?\s*(?:/\s*người)?\s*[:：]\s*(?P<v>[^\n]+)"
)


def _to_vnd(num: str, unit: Optional[str]) -> int:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", num):
        value = float(re.sub(r"[.,]", "", num))
    else:
        value = float(num.replace(",", "."))
    unit = (unit or "").lower()
    if re.match(_UNIT_K, unit, re.I):
        value *= 1_000
    elif re.match(_UNIT_M, unit, re.I):
        value *= 1_000_000
    return int(round(value))


def _plausible(v: int) -> bool:
    return 1_000 <= v <= 50_000_000


def _parse_price_value(value: str) -> Optional[Tuple[int, int]]:
    m = _PRICE_RANGE.search(value)
    if m:
        unit_b = m.group("ub")
        unit_a = m.group("ua") or unit_b  # "20 - 55k" -> both in k
        lo, hi = _to_vnd(m.group("a"), unit_a), _to_vnd(m.group("b"), unit_b)
        if _plausible(lo) and _plausible(hi) and lo <= hi:
            return lo, hi
    m = _PRICE_ONE.search(value)
    if m:
        v = _to_vnd(m.group("a"), m.group("u"))
        if _plausible(v):
            return v, v
    return None


def parse_price(text: str) -> Optional[Tuple[int, int]]:
    """(min, max) in VND. Labelled lines win; otherwise the first amount with a currency unit."""
    if not text:
        return None
    
    text = re.sub(r"(?<=\d)\s+(?=[.,]\d{3}(?!\d))", "", text)  # "45 .000" -> "45.000"
    for m in _PRICE_LABEL.finditer(text):
        value = _FIELD_STOP.split(m.group("v"), maxsplit=1)[0]
        got = _parse_price_value(value)
        if got:
            return got
    return _parse_price_value(text)


def format_price(lo: int, hi: int) -> str:
    def fmt(n: int) -> str:
        return f"{n:,}".replace(",", ".")

    return f"{fmt(lo)} VNĐ" if lo == hi else f"{fmt(lo)} - {fmt(hi)} VNĐ"


def extract_price(text: str) -> str:
    got = parse_price(text)
    return format_price(*got) if got else ""


# -------------------------------------------------------------------------- hours
_T = r"(?P<h{i}>\d{{1,2}})\s*(?:h|giờ|:)\s*(?P<m{i}>\d{{2}})?\s*(?P<p{i}>[AaPp][Mm])?"
_HOURS_RANGE = re.compile(
    rf"(?<![\d:]){_T.format(i=1)}(?:\s*{_SEP}\s*|\s+){_T.format(i=2)}(?!\d)", re.I
)
_HOURS_FROM = re.compile(
    rf"(?:từ|from)\s*(?<![\d:])(?P<h>\d{{1,2}})\s*(?:h|giờ|:)\s*(?P<m>\d{{2}})?(?!\d)", re.I
)
_HOURS_24 = re.compile(r"(?i)24\s*/\s*24|24\s*/\s*7|24h(?!\s*\d)|24\s*giờ|cả ngày")
_HOURS_LABEL = re.compile(
    rf"(?m)^{_BULLETS}(?i:giờ\s*(?:mở cửa|phục vụ|hoạt động)|thời gian(?:\s*(?:mở cửa|hoạt động))?|opening hours)"
    r"\s*[:：]\s*(?P<v>[^\n]+)"
)
_HOURS_SENTENCE = re.compile(r"(?i)(?:mở cửa|phục vụ|hoạt động|open)[^\n]{0,60}")


def _hhmm(h: str, m: Optional[str], ampm: Optional[str]) -> Optional[str]:
    hour, minute = int(h), int(m or 0)
    if minute > 59 or hour > 24:
        return None
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    hour %= 24
    return f"{hour:02d}:{minute:02d}"

def _span_minutes(a: str, b: str) -> int:
    (ha, ma), (hb, mb) = (map(int, a.split(":")), map(int, b.split(":")))
    return ((hb * 60 + mb) - (ha * 60 + ma)) % (24 * 60)

def _parse_hours_value(value: str) -> str:
    if _HOURS_24.search(value):
        return "Mở cửa 24h"
    ranges: List[str] = []
    for m in _HOURS_RANGE.finditer(value):
        a = _hhmm(m.group("h1"), m.group("m1"), m.group("p1"))
        b = _hhmm(m.group("h2"), m.group("m2"), m.group("p2"))
        if a and b and _span_minutes(a, b) >= 60:  # a span under 1h is a typo, not opening hours
            r = f"{a} - {b}"
            if r not in ranges:
                ranges.append(r)
    if ranges:
        return "; ".join(ranges[:3])
    m = _HOURS_FROM.search(value)
    if m:
        t = _hhmm(m.group("h"), m.group("m"), None)
        if t:
            return f"Từ {t}"
    return ""


def normalize_hours(value: str) -> str:
    """Canonical hours string ("07:00 - 23:00", "Mở cửa 24h") or "" if nothing valid."""
    return _parse_hours_value(value or "")


def extract_hours(text: str) -> str:
    """Only labelled opening hours. Bare times such as '20h00 có live' are performance times."""
    if not text:
        return ""
    for m in _HOURS_LABEL.finditer(text):
        got = _parse_hours_value(_FIELD_STOP.split(m.group("v"), maxsplit=1)[0])
        if got:
            return got
    for m in _HOURS_SENTENCE.finditer(text):
        got = _parse_hours_value(m.group(0))
        if got:
            return got
    return ""


# -------------------------------------------------------------------- description
_LABEL_LINE = re.compile(
    r"(?i)^[\s•·\-–—*▪■◆➤>🞂]*(?:địa chỉ|address|giờ\s*(?:mở cửa|phục vụ|hoạt động)|opening hours"
    r"|mức giá|khoảng giá|giá tham khảo|giá vé|giá|price)\s*[:：]"
)
_CREDIT_LINE = re.compile(r"(?i)^[\s_*(\[]*(?:ảnh|nguồn|source|photo|credit|tham khảo|xem thêm)\s*[:：]")
_CREDIT_INLINE = re.compile(r"(?i)[_(\[]\s*(?:ảnh|nguồn ảnh|photo)\s*:\s*[^\n_)\]]{0,60}[_)\]]")


_PRICE_PREFIX = re.compile(
    r"(?i)^[\s•·\-–—*▪■◆➤>🞂]*(?:mức giá|khoảng giá|giá tham khảo|giá vé|giá)\b[^:：\n]{0,30}[:：]\s*"
)
def _unnamed(pattern: str) -> str:
    return re.sub(r"\(\?P<\w+>", "(?:", pattern)  # group names cannot repeat inside one pattern


_PRICE_AMOUNT = re.compile(
    rf"(?:(?i:từ|khoảng)\s+)?(?:{_unnamed(_PRICE_RANGE.pattern)}|{_unnamed(_PRICE_ONE.pattern)})"
    r"\s*(?:/\s*người)?[.;,]?\s*",
    re.I,
)
# Everything after these markers is page furniture (ads, related products, contact box).
_CUT_MARKERS = re.compile(
    r"\s*(?:Các sản phẩm được yêu thích|Sản phẩm liên quan|Thông tin liên hệ|Bài viết liên quan|"
    r"Có thể bạn (?:quan tâm|cũng thích)|Xem thêm\s*:|Đọc thêm\s*:).*$",
    re.S,
)


def _strip_price_prefix(line: str):
    """'Giá đồ uống từ: 20.000đ - 55.000đ. Quán ...' -> 'Quán ...'. None if not a price line."""
    m = _PRICE_PREFIX.match(line)
    if not m:
        return None
    rest = line[m.end():]
    amount = _PRICE_AMOUNT.match(rest)
    return rest[amount.end():].strip() if amount else ""  # unreadable price: drop the line


def build_description(raw_text: str) -> str:
    """Raw text minus label lines (address/price/hours), image credits and page furniture.
    Never truncated."""
    kept: List[str] = []
    for line in (raw_text or "").split("\n"):
        line = line.strip()
        if not line or _CREDIT_LINE.match(line):
            continue
        stripped = _strip_price_prefix(line)
        if stripped is not None:
            line = stripped
        elif _LABEL_LINE.match(line):
            continue
        line = _CREDIT_INLINE.sub("", line).strip()
        if line:
            kept.append(line)
    text = re.sub(r"\s+", " ", " ".join(kept)).strip()
    return _CUT_MARKERS.sub("", text).strip()