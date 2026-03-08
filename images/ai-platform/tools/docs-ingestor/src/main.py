import os
import re
import uuid
import gzip
from collections import deque
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup


def env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


SOURCE_NAME = env("SOURCE_NAME", "unknown")
TENANT = env("TENANT", SOURCE_NAME)
START_URL = env("START_URL", "")
ALLOWED_HOSTS = {h.strip() for h in env("ALLOWED_HOSTS", "").split(",") if h.strip()}
URL_PREFIXES = [p.strip() for p in env("URL_PREFIXES", "").split(",") if p.strip()]
COLLECTION = env("QDRANT_COLLECTION", f"docs-{SOURCE_NAME}")
QDRANT_URL = env("QDRANT_URL", "http://qdrant.ai-platform.svc.cluster.local:6333")
EMBEDDING_BASE_URL = env("EMBEDDING_BASE_URL", "http://embedding-api.ai-platform.svc.cluster.local:80")
EMBED_MODEL = env("EMBEDDING_MODEL", "BAAI/bge-m3")
MAX_PAGES = int(env("MAX_PAGES", "400"))
CHUNK_SIZE = int(env("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(env("CHUNK_OVERLAP", "150"))
REQUEST_TIMEOUT = float(env("REQUEST_TIMEOUT_SECONDS", "20"))
USER_AGENT = env("USER_AGENT", "homelab-docs-ingestor/0.1")
SITEMAP_URL = env("SITEMAP_URL", "")


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if ALLOWED_HOSTS and parsed.netloc not in ALLOWED_HOSTS:
        return False
    if URL_PREFIXES and not any(url.startswith(prefix) for prefix in URL_PREFIXES):
        return False
    if any(x in url for x in ("#", "?view=", "?tabs=")):
        return False
    if re.search(r"\.(png|jpg|jpeg|gif|svg|pdf|zip|tar|gz|mp4|mp3)$", parsed.path, re.IGNORECASE):
        return False
    return True


def normalize_url(url: str) -> str:
    p = urlparse(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"{p.scheme}://{p.netloc}{path}"


def html_to_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    return title, text


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    i = 0
    while i < len(text):
        part = text[i : i + chunk_size].strip()
        if part:
            chunks.append(part)
        i += step
    return chunks


def extract_links(base_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if is_allowed_url(absolute):
            links.append(absolute)
    return links


def point_id(url: str, chunk_index: int) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE_NAME}:{url}:{chunk_index}").hex


def derive_service(url: str) -> str:
    p = urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    if p.netloc == "docs.aws.amazon.com":
        return segs[0].lower() if segs else "unknown"
    if p.netloc == "learn.microsoft.com":
        # e.g. /en-us/azure/virtual-machines/...
        if "azure" in segs:
            idx = segs.index("azure")
            if idx + 1 < len(segs):
                return segs[idx + 1].lower()
        return "azure"
    return "unknown"


def qdrant_filter_for_url(url: str) -> dict:
    return {
        "must": [
            {"key": "tenant", "match": {"value": TENANT}},
            {"key": "source", "match": {"value": SOURCE_NAME}},
            {"key": "url", "match": {"value": url}},
        ]
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run() -> None:
    if not START_URL:
        raise RuntimeError("START_URL is required")
    if not ALLOWED_HOSTS:
        raise RuntimeError("ALLOWED_HOSTS is required")

    headers = {"User-Agent": USER_AGENT}
    visited: set[str] = set()
    queue: deque[str] = deque([normalize_url(START_URL)])
    pages_done = 0

    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
        if SITEMAP_URL:
            for u in _urls_from_sitemap(client, SITEMAP_URL, MAX_PAGES * 3):
                if is_allowed_url(u) and u not in visited:
                    queue.append(u)
            print(f"[{SOURCE_NAME}] loaded sitemap urls={len(queue)} from={SITEMAP_URL}")

        while queue and pages_done < MAX_PAGES:
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            try:
                res = client.get(url)
                if res.status_code != 200 or "text/html" not in res.headers.get("content-type", ""):
                    continue
            except Exception:
                continue

            # Always discover deeper docs links even if this page is not indexable.
            for link in extract_links(url, res.text):
                if link not in visited:
                    queue.append(link)

            title, text = html_to_text(res.text)
            if len(text) < 200:
                print(f"[{SOURCE_NAME}] skip short page {url} chars={len(text)}")
                continue

            chunks = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
            if not chunks:
                print(f"[{SOURCE_NAME}] skip empty chunks {url}")
                continue

            emb = client.post(
                f"{EMBEDDING_BASE_URL}/v1/embeddings",
                json={"input": chunks, "model": EMBED_MODEL},
            )
            emb.raise_for_status()
            embeddings = [x.get("embedding", []) for x in emb.json().get("data", [])]
            if not embeddings or not embeddings[0]:
                continue

            dim = len(embeddings[0])
            coll_resp = client.get(f"{QDRANT_URL}/collections/{COLLECTION}")
            if coll_resp.status_code == 404:
                create_resp = client.put(
                    f"{QDRANT_URL}/collections/{COLLECTION}",
                    json={"vectors": {"size": dim, "distance": "Cosine"}},
                )
                create_resp.raise_for_status()
            elif coll_resp.status_code != 200:
                coll_resp.raise_for_status()

            delete_resp = client.post(
                f"{QDRANT_URL}/collections/{COLLECTION}/points/delete?wait=true",
                json={"filter": qdrant_filter_for_url(url)},
            )
            delete_resp.raise_for_status()

            points = []
            for idx, (chunk, vector) in enumerate(zip(chunks, embeddings)):
                points.append(
                    {
                        "id": point_id(url, idx),
                        "vector": vector,
                        "payload": {
                            "tenant": TENANT,
                            "source": SOURCE_NAME,
                            "url": url,
                            "title": title,
                            "service": derive_service(url),
                            "chunk_index": idx,
                            "text": chunk,
                            "fetched_at": now_iso(),
                        },
                    }
                )

            upsert_resp = client.put(
                f"{QDRANT_URL}/collections/{COLLECTION}/points?wait=true",
                json={"points": points},
            )
            upsert_resp.raise_for_status()
            pages_done += 1
            print(f"[{SOURCE_NAME}] indexed {url} chunks={len(points)} total_pages={pages_done}")

    print(f"[{SOURCE_NAME}] completed: indexed_pages={pages_done} visited={len(visited)} collection={COLLECTION}")


def _decode_xml_bytes(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


def _urls_from_sitemap(client: httpx.Client, sitemap_url: str, max_urls: int) -> list[str]:
    discovered: list[str] = []
    pending: deque[str] = deque([sitemap_url])
    seen_maps: set[str] = set()

    while pending and len(discovered) < max_urls:
        sm_url = pending.popleft()
        if sm_url in seen_maps:
            continue
        seen_maps.add(sm_url)
        try:
            res = client.get(sm_url)
            if res.status_code != 200:
                continue
            raw = _decode_xml_bytes(res.content)
            root = ET.fromstring(raw)
        except Exception:
            continue

        tag = root.tag.lower()
        if tag.endswith("sitemapindex"):
            for node in root.findall(".//{*}sitemap/{*}loc"):
                if node.text:
                    pending.append(node.text.strip())
        elif tag.endswith("urlset"):
            for node in root.findall(".//{*}url/{*}loc"):
                if node.text:
                    discovered.append(normalize_url(node.text.strip()))
                    if len(discovered) >= max_urls:
                        break
    return discovered


if __name__ == "__main__":
    run()
