
from pathlib import Path
import re

CLEANED_DIR = Path("/home/michael/Documents/wrong_way_to_use_healing_magic/cleaned_files_v2")

def get_volume_from_filename(filename: str) -> str:
    match = re.match(r'^(\d+)_vol\.', filename)
    if match:
        return f"vol_{match.group(1).zfill(2)}"
    return "vol_unknown"

filename = "10_vol._3_chapter.pdf"
vol_key = get_volume_from_filename(filename)
vol_dir = CLEANED_DIR / vol_key
md_path = vol_dir / (filename.replace(".pdf", "") + ".md")

print(f"vol_key: {vol_key}")
print(f"vol_dir: {vol_dir}")
print(f"md_path: {md_path}")
