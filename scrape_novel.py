import urllib.request
import urllib.parse
import json
import re
import os
import subprocess
import time
import random

def get_chapters(book_id):
    chapters = []
    # Fetch from all 12 paginations (adjust if needed, but 12 covers the current chapters)
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
            
    return chapters

def scrape_novel():
    print("Fetching chapter list...")
    chapters = get_chapters(62)
    print(f"Total chapters found: {len(chapters)}")
    
    # Filter chapters for Volume >= 10
    vol_chapters = []
    for link, text in chapters:
        m = re.search(r'(\d+)\s+vol\.', text, re.IGNORECASE)
        if m:
            vol = int(m.group(1))
            if vol >= 10:
                # Use regex to find chapter number too for clean filenames
                ch_m = re.search(r'(\d+)\s+chapter', text, re.IGNORECASE)
                ch = int(ch_m.group(1)) if ch_m else 0
                vol_chapters.append((link, text, vol, ch))
                
    # Sort them in chronological order: ascending by volume, then by chapter
    vol_chapters.sort(key=lambda x: (x[2], x[3]))
    
    print(f"Total chapters for Volume >= 10: {len(vol_chapters)}")
    
    out_dir = "/home/michael/Documents/wrong_way_to_use_healing_magic/raw_files"
    os.makedirs(out_dir, exist_ok=True)
    
    # Process each chapter
    for i, (link, text, vol, ch) in enumerate(vol_chapters):
        full_url = f"https://novelight.net{link}"
        
        # Clean up text for filename
        clean_name = text.split('\n')[0].strip().replace(' ', '_').replace('/', '-').replace('"', '')
        if not clean_name:
            clean_name = f"Vol_{vol}_Ch_{ch}"
            
        out_file = os.path.join(out_dir, f"{clean_name}.pdf")
        
        if os.path.exists(out_file):
            print(f"Skipping {clean_name}, already exists.")
            continue
            
        print(f"[{i+1}/{len(vol_chapters)}] Downloading: {clean_name}")
        
        cmd = [
            "google-chrome",
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            f"--print-to-pdf={out_file}",
            full_url
        ]
        
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"  -> Saved to {out_file}")
        except subprocess.CalledProcessError as e:
            print(f"  -> Error running Chrome for {clean_name}")
            
        # Random wait between 5 and 10 seconds to mimic human behavior
        if i < len(vol_chapters) - 1:
            wait_time = random.uniform(5.0, 10.0)
            print(f"  -> Waiting {wait_time:.2f} seconds before next chapter...\n")
            time.sleep(wait_time)

if __name__ == "__main__":
    scrape_novel()
