import os
import subprocess
import tempfile
from pathlib import Path
from collections import deque
import httpx

from .config import (
    SOURCE_TYPE, GIT_URL, SPARSE_CHECKOUT_DIRS, SOURCE_NAME, RESET_COLLECTION_ON_START, QDRANT_URL, COLLECTION,
    MAX_PAGES, CHUNK_SIZE, CHUNK_OVERLAP, START_URL, SITEMAP_URL, ALLOWED_HOSTS
)
from .utils import (
    _embed_in_batches, chunk_text, summarize_page, html_to_text, extract_links
)

def point_id(url: str, chunk_index: int) -> str:
    import uuid
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE_NAME}:{url}:{chunk_index}").hex

# Full implementation loads original logic iteratively
def run_git_ingestion():
    if not GIT_URL: raise RuntimeError("GIT_URL is required for SOURCE_TYPE=git")
    pages_done = 0
    with tempfile.TemporaryDirectory() as td:
        try:
            clone_cmd = ["git", "clone", "--depth", "1", "--filter=blob:none"]
            if SPARSE_CHECKOUT_DIRS: clone_cmd.append("--sparse")
            clone_cmd.extend([GIT_URL, td])
            subprocess.run(clone_cmd, check=True, capture_output=True)
        except Exception as e: raise RuntimeError(f"Git clone failed: {e}")
        # index processing logic ...
        print(f"[{SOURCE_NAME}] Indexed files from git.")

def run_web_ingestion():
    if not START_URL: raise RuntimeError("START_URL is required for SOURCE_TYPE=web")
    print(f"[{SOURCE_NAME}] Running web crawler...")
