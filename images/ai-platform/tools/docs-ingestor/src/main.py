import os
import re
import uuid
import gzip
import subprocess
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup


def env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


SOURCE_TYPE = env("SOURCE_TYPE", "web").lower()
SOURCE_NAME = env("SOURCE_NAME", "unknown")
TENANT = env("TENANT", SOURCE_NAME)
START_URL = env("START_URL", "")
GIT_URL = env("GIT_URL", "")
ALLOWED_HOSTS = {h.strip() for h in env("ALLOWED_HOSTS", "").split(",") if h.strip()}
URL_PREFIXES = [p.strip() for p in env("URL_PREFIXES", "").split(",") if p.strip()]
COLLECTION = env("QDRANT_COLLECTION", f"docs-{SOURCE_NAME}")
QDRANT_URL = env("QDRANT_URL", "http://qdrant.ai-platform.svc.cluster.local:6333")
EMBEDDING_BASE_URL = env("EMBEDDING_BASE_URL", "http://embedding-api.ai-platform.svc.cluster.local:80")
EMBED_MODEL = env("EMBEDDING_MODEL", "BAAI/bge-m3")
MAX_PAGES = int(env("MAX_PAGES", "400"))
CHUNK_SIZE = int(env("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(env("CHUNK_OVERLAP", "150"))
EMBED_BATCH_SIZE = int(env("EMBED_BATCH_SIZE", "16"))
EMBED_WORKERS = int(env("EMBED_WORKERS", "4"))
REQUEST_TIMEOUT = float(env("REQUEST_TIMEOUT_SECONDS", "20"))
USER_AGENT = env("USER_AGENT", "homelab-docs-ingestor/0.1")
SITEMAP_URL = env("SITEMAP_URL", "")
RESET_COLLECTION_ON_START = env("RESET_COLLECTION_ON_START", "false").lower() == "true"
KEYWORD_COUNT = int(env("KEYWORD_COUNT", "12"))
PRIORITY_KEYWORDS = [k.strip().lower() for k in env("PRIORITY_KEYWORDS", "").split(",") if k.strip()]


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


def summary_point_id(url: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE_NAME}:{url}:summary").hex


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


def extract_keywords(title: str, text: str, service: str, n: int) -> list[str]:
    raw = f"{title} {text[:12000]}".lower()
    words = re.findall(r"[a-z0-9][a-z0-9-]{2,}", raw)
    stop = {
        "this", "that", "with", "from", "your", "have", "will", "into", "about", "using", "used", "than",
        "where", "when", "which", "while", "what", "they", "their", "them", "these", "those", "also", "more",
        "aws", "azure", "service", "guide", "reference", "api", "latest", "docs", "documentation",
    }
    freq: dict[str, int] = {}
    for w in words:
        if w in stop:
            continue
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)
    out = [service] if service and service != "unknown" else []
    out.extend([w for w, _ in ranked[: max(0, n - len(out))]])
    return out[:n]


def summarize_page(title: str, text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return title
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    picked: list[str] = []
    budget = 1100
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        picked.append(s)
        if sum(len(x) + 1 for x in picked) >= budget:
            break
        if len(picked) >= 6:
            break
    core = " ".join(picked).strip()
    if title:
        return f"{title}. {core}"[:1400]
    return core[:1400]


def parse_md_title(text: str, filename: str) -> str:
    match = re.search(r"^#\s+(.*?)$", text, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return filename


def run() -> None:
    if SOURCE_TYPE == "git":
        run_git_ingestion()
    else:
        run_web_ingestion()


def run_git_ingestion() -> None:
    if not GIT_URL:
        raise RuntimeError("GIT_URL is required for SOURCE_TYPE=git")

    pages_done = 0
    with tempfile.TemporaryDirectory() as td:
        print(f"[{SOURCE_NAME}] cloning {GIT_URL} into {td}...")
        try:
            # Use tree-less clone to avoid downloading massive blobs (media/images)
            subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", GIT_URL, td], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git clone failed: {e.stderr.decode()}")

        root = Path(td)
        md_files = list(root.rglob("*.md")) + list(root.rglob("*.mdx"))
        print(f"[{SOURCE_NAME}] found {len(md_files)} markdown files")

        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            if RESET_COLLECTION_ON_START:
                d = client.delete(f"{QDRANT_URL}/collections/{COLLECTION}")
                if d.status_code not in (200, 404):
                    d.raise_for_status()

            for f in md_files:
                if pages_done >= MAX_PAGES:
                    break
                
                try:
                    text = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                
                if len(text) < 50:
                    continue

                rel_path = str(f.relative_to(root))
                # treat the file path as the "url" for consistency
                virtual_url = f"git://{SOURCE_NAME}/{rel_path}"
                title = parse_md_title(text, f.name)
                
                chunks = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
                if not chunks:
                    continue

                embeddings = _embed_in_batches(client, chunks)
                if not embeddings or not embeddings[0]:
                    continue

                service = derive_service(virtual_url)
                keywords = extract_keywords(title, text, service, KEYWORD_COUNT)
                summary = summarize_page(title, text)
                summary_vecs = _embed_in_batches(client, [f"{title}\n\n{summary}\n\nkeywords: {' '.join(keywords)}"])
                if not summary_vecs or not summary_vecs[0]:
                    continue

                dim = len(embeddings[0])
                coll_resp = client.get(f"{QDRANT_URL}/collections/{COLLECTION}")
                if coll_resp.status_code == 404:
                    create_resp = client.put(
                        f"{QDRANT_URL}/collections/{COLLECTION}",
                        json={"vectors": {"size": dim, "distance": "Cosine"}},
                    )
                    create_resp.raise_for_status()

                points = []
                points.append(
                    {
                        "id": summary_point_id(virtual_url),
                        "vector": summary_vecs[0],
                        "payload": {
                            "tenant": TENANT,
                            "source": SOURCE_NAME,
                            "url": virtual_url,
                            "title": title,
                            "service": service,
                            "doc_type": "summary",
                            "summary": summary,
                            "keywords": keywords,
                            "fetched_at": now_iso(),
                        },
                    }
                )
                for idx, (chunk, vector) in enumerate(zip(chunks, embeddings)):
                    points.append(
                        {
                            "id": point_id(virtual_url, idx),
                            "vector": vector,
                            "payload": {
                                "tenant": TENANT,
                                "source": SOURCE_NAME,
                                "url": virtual_url,
                                "title": title,
                                "service": service,
                                "doc_type": "chunk",
                                "chunk_index": idx,
                                "summary": summary,
                                "keywords": keywords,
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
                print(f"[{SOURCE_NAME}] indexed {virtual_url} chunks={len(points)} pages_done={pages_done}")

    print(f"[{SOURCE_NAME}] git ingestion completed: pages={pages_done} collection={COLLECTION}")


def run_web_ingestion() -> None:
    if not START_URL:
        raise RuntimeError("START_URL is required for SOURCE_TYPE=web")
    if not ALLOWED_HOSTS:
        raise RuntimeError("ALLOWED_HOSTS is required for SOURCE_TYPE=web")

    headers = {"User-Agent": USER_AGENT}
    visited: set[str] = set()
    queue: deque[str] = deque([normalize_url(START_URL)])
    pages_done = 0

    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
        if RESET_COLLECTION_ON_START:
            d = client.delete(f"{QDRANT_URL}/collections/{COLLECTION}")
            if d.status_code not in (200, 404):
                d.raise_for_status()
            print(f"[{SOURCE_NAME}] reset collection={COLLECTION}")

        if SITEMAP_URL:
            sitemap_urls = _urls_from_sitemap(client, SITEMAP_URL, MAX_PAGES * 20)
            balanced_urls = _interleave_urls_by_service(sitemap_urls, MAX_PAGES * 3)
            balanced_urls = _prioritize_urls_by_keywords(balanced_urls, PRIORITY_KEYWORDS)
            for u in balanced_urls:
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

            embeddings = _embed_in_batches(client, chunks)
            if not embeddings or not embeddings[0]:
                continue
            service = derive_service(url)
            keywords = extract_keywords(title, text, service, KEYWORD_COUNT)
            summary = summarize_page(title, text)
            summary_vecs = _embed_in_batches(client, [f"{title}\n\n{summary}\n\nkeywords: {' '.join(keywords)}"])
            if not summary_vecs or not summary_vecs[0]:
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
            points.append(
                {
                    "id": summary_point_id(url),
                    "vector": summary_vecs[0],
                    "payload": {
                        "tenant": TENANT,
                        "source": SOURCE_NAME,
                        "url": url,
                        "title": title,
                        "service": service,
                        "doc_type": "summary",
                        "summary": summary,
                        "keywords": keywords,
                        "fetched_at": now_iso(),
                    },
                }
            )
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
                            "service": service,
                            "doc_type": "chunk",
                            "chunk_index": idx,
                            "summary": summary,
                            "keywords": keywords,
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


def _interleave_urls_by_service(urls: list[str], max_urls: int) -> list[str]:
    by_service: dict[str, deque[str]] = {}
    order: list[str] = []
    for u in urls:
        svc = derive_service(u)
        if svc not in by_service:
            by_service[svc] = deque()
            order.append(svc)
        by_service[svc].append(u)

    out: list[str] = []
    while len(out) < max_urls:
        progressed = False
        for svc in order:
            q = by_service[svc]
            if q:
                out.append(q.popleft())
                progressed = True
                if len(out) >= max_urls:
                    break
        if not progressed:
            break
    return out


def _embed_in_batches(client: httpx.Client, chunks: list[str]) -> list[list[float]]:
    batch_size = max(1, EMBED_BATCH_SIZE)
    workers = max(1, EMBED_WORKERS)
    batches: list[tuple[int, list[str]]] = []
    i = 0
    while i < len(chunks):
        batches.append((i, chunks[i : i + batch_size]))
        i += batch_size

    results: dict[int, list[list[float]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_embed_batch_with_retry, batch): idx
            for idx, batch in batches
        }
        for fut in as_completed(futs):
            idx = futs[fut]
            results[idx] = fut.result()

    vectors: list[list[float]] = []
    for idx, _ in sorted(batches, key=lambda x: x[0]):
        vectors.extend(results.get(idx, []))
    return vectors


def _embed_batch_with_retry(batch: list[str]) -> list[list[float]]:
    # Retry recursively with smaller payloads on 413.
    with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
        emb = c.post(
            f"{EMBEDDING_BASE_URL}/v1/embeddings",
            json={"input": batch, "model": EMBED_MODEL},
        )
        if emb.status_code == 413:
            if len(batch) == 1:
                raise httpx.HTTPStatusError(
                    "413 for single chunk; reduce CHUNK_SIZE",
                    request=emb.request,
                    response=emb,
                )
            mid = len(batch) // 2
            left = _embed_batch_with_retry(batch[:mid])
            right = _embed_batch_with_retry(batch[mid:])
            return left + right
        emb.raise_for_status()
        data = emb.json().get("data", [])
        return [x.get("embedding", []) for x in data]


def _prioritize_urls_by_keywords(urls: list[str], keywords: list[str]) -> list[str]:
    if not keywords:
        return urls
    hi: list[str] = []
    lo: list[str] = []
    for u in urls:
        lu = u.lower()
        if any(k in lu for k in keywords):
            hi.append(u)
        else:
            lo.append(u)
    return hi + lo


if __name__ == "__main__":
    run()
