import urllib.request
import urllib.parse
import json
import re
import os
import subprocess
import time

def test_scrape():
    # 1. Fetch pages to get chapters
    book_id = 62
    chapters = []
    
    # We will just fetch page 10, 11, 12, etc until we find Volume 10 chapters
    for page in range(1, 13):
        url = f"https://novelight.net/book/ajax/chapter-pagination?book_id={book_id}&page={page}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest'})
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                html = data.get('html', '')
                
                # Extract links
                matches = re.findall(r'<a[^>]*href=["\'](/book/chapter/\d+)["\'][^>]*>(.*?)</a>', html, re.DOTALL)
                for link, text in matches:
                    text_clean = re.sub(r'<[^>]+>', '', text).strip()
                    chapters.append((link, text_clean))
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            
    print(f"Total chapters found: {len(chapters)}")
    
    # 2. Filter chapters for Volume >= 10
    vol_chapters = []
    for link, text in chapters:
        # Looking for "10 vol." or similar
        m = re.search(r'(\d+)\s+vol\.', text, re.IGNORECASE)
        if m:
            vol = int(m.group(1))
            if vol >= 10:
                vol_chapters.append((link, text, vol))
                
    print(f"Total chapters for Volume >= 10: {len(vol_chapters)}")
    
    if not vol_chapters:
        print("No Volume 10+ chapters found. Exiting test.")
        return
        
    # Sort them so we get the earliest one
    # The list is likely reverse chronological, so let's just take the last one in the vol_chapters
    target = vol_chapters[-1]
    link, text, vol = target
    
    full_url = f"https://novelight.net{link}"
    print(f"Testing with chapter: {text} ({full_url})")
    
    # 3. Create directory
    out_dir = "/home/michael/Documents/wrong_way_to_use_healing_magic/raw_files"
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "test_vol10_ch.pdf")
    
    # 4. Use google-chrome to print to PDF
    print(f"Running google-chrome --headless --print-to-pdf={out_file} '{full_url}'")
    
    # Need to run google-chrome with no-sandbox as we are probably root or restricted
    cmd = [
        "google-chrome",
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        f"--print-to-pdf={out_file}",
        full_url
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"Successfully generated PDF: {out_file}")
    except subprocess.CalledProcessError as e:
        print(f"Error running Chrome: {e.stderr.decode()}")
        
if __name__ == "__main__":
    test_scrape()
