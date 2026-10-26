"""Debug script to inspect HTML structure around a specific tea room heading."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.crawler.base_crawler import BaseCrawler


async def debug_images(url: str, target_name: str = "Acoustic"):
    """Fetch page and print HTML around target heading."""
    crawler = BaseCrawler(name="debug")
    
    async with crawler:
        html = await crawler.fetch_html(url)
        soup = crawler.parse_html(html)
        
        # Tìm heading chứa target_name
        headings = soup.find_all(["h2", "h3"])
        target_heading = None
        
        for h in headings:
            text = h.get_text(strip=True)
            if target_name.lower() in text.lower():
                target_heading = h
                print(f"(ok found) Found heading: '{text}'")
                break
        
        if not target_heading:
            print(f"[X] Could not find heading containing '{target_name}'")
            return
        
        # In ra HTML của heading và 10 sibling tiếp theo
        print("\n" + "=" * 80)
        print("HTML AROUND TARGET HEADING:")
        print("=" * 80)
        
        # In heading
        print(f"\n[HEADING]:\n{target_heading}")
        
        # In 10 sibling tiếp theo
        current = target_heading.find_next_sibling()
        count = 0
        while current and count < 10:
            print(f"\n[SIBLING {count + 1} - <{current.name}>]:")
            print(str(current)[:500])  # Giới hạn 500 ký tự
            
            # Tìm tất cả <img> trong sibling này
            imgs = current.find_all("img")
            if imgs:
                print(f"  (ảnh) Found {len(imgs)} image(s):")
                for img in imgs:
                    print(f"    - src: {img.get('src', 'N/A')}")
                    print(f"    - data-src: {img.get('data-src', 'N/A')}")
                    print(f"    - data-lazy-src: {img.get('data-lazy-src', 'N/A')}")
                    print(f"    - alt: {img.get('alt', 'N/A')}")
            
            current = current.find_next_sibling()
            count += 1
        
        # Tìm TẤT CẢ ảnh trong toàn bộ section (từ heading này đến heading tiếp theo)
        print("\n" + "=" * 80)
        print("ALL IMAGES BETWEEN THIS HEADING AND NEXT HEADING:")
        print("=" * 80)
        
        next_heading = target_heading.find_next(["h2", "h3"])
        section_html = ""
        current = target_heading
        while current and current != next_heading:
            section_html += str(current)
            current = current.find_next()
        
        section_soup = crawler.parse_html(section_html)
        all_imgs = section_soup.find_all("img")
        
        if all_imgs:
            print(f"[OK] Found {len(all_imgs)} image(s) in section:")
            for i, img in enumerate(all_imgs):
                src = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
                print(f"  [{i+1}] src={src}")
                print(f"      alt={img.get('alt', 'N/A')}")
        else:
            print("[lỗi] No images found in this section!")


if __name__ == "__main__":
    url = "https://mytour.vn/vi/blog/bai-viet/10-dia-diem-phong-tra-am-nhac-tuyet-voi-o-ha-noi-de-ban-thu-gian-digifood.html"
    asyncio.run(debug_images(url, "Acoustic"))