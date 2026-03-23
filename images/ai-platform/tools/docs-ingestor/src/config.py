import os
from urllib.parse import urlparse
import re

def env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()

SOURCE_TYPE = env("SOURCE_TYPE", "web").lower()
SOURCE_NAME = env("SOURCE_NAME", "unknown")
TENANT = env("TENANT", SOURCE_NAME)
START_URL = env("START_URL", "")
GIT_URL = env("GIT_URL", "")
SPARSE_CHECKOUT_DIRS = env("SPARSE_CHECKOUT_DIRS", "")
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

def derive_service(url: str) -> str:
    p = urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    if p.netloc == "docs.aws.amazon.com":
        return segs[0].lower() if segs else "unknown"
    if p.netloc == "learn.microsoft.com":
        for i, s in enumerate(segs):
            if s == "azure" and i + 1 < len(segs): return segs[i + 1].lower()
        return "azure"
    return "unknown"
