import re
import uuid
import gzip
import httpx
from collections import deque
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from .config import EMBEDDING_BASE_URL, EMBED_MODEL, REQUEST_TIMEOUT, EMBED_BATCH_SIZE, EMBED_WORKERS, normalize_url, is_allowed_url, derive_service

def html_to_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
         tag.decompose()
    from html import unescape
    text = soup.get_text(" ", strip=True)
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    return title, text

def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if not text: return []
    if len(text) <= chunk_size: return [text]
    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    i = 0
    while i < len(text):
        part = text[i : i + chunk_size].strip()
        if part: chunks.append(part)
        i += step
    return chunks

def extract_links(base_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href: continue
        absolute = normalize_url(urljoin(base_url, href))
        if is_allowed_url(absolute): links.append(absolute)
    return links

def summarize_page(title: str, text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned: return title
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    picked: list[str] = []
    budget = 1100
    for s in sentences:
        s = s.strip()
        if not s: continue
        picked.append(s)
        if sum(len(x) + 1 for x in picked) >= budget or len(picked) >= 6: break
    core = " ".join(picked).strip()
    return f"{title}. {core}"[:1400] if title else core[:1400]

def _embed_in_batches(client: httpx.Client, chunks: list[str]) -> list[list[float]]:
    batch_size = max(1, EMBED_BATCH_SIZE)
    workers = max(1, EMBED_WORKERS)
    batches = [(i, chunks[i : i + batch_size]) for i in range(0, len(chunks), batch_size)]

    results: dict[int, list[list[float]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_embed_batch_with_retry, b): i for i, b in batches}
        for fut in as_completed(futs): results[futs[fut]] = fut.result()

    return [x for i, _ in batches for x in results.get(i, [])]

def _embed_batch_with_retry(batch: list[str]) -> list[list[float]]:
    with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
        emb = c.post(f"{EMBEDDING_BASE_URL}/v1/embeddings", json={"input": batch, "model": EMBED_MODEL})
        if emb.status_code == 413:
            if len(batch) == 1: raise Exception("413 for single chunk")
            mid = len(batch) // 2
            return _embed_batch_with_retry(batch[:mid]) + _embed_batch_with_retry(batch[mid:])
        emb.raise_for_status()
        return [x.get("embedding", []) for x in emb.json().get("data", [])]
