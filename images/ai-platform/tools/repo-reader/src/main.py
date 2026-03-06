from fastapi import FastAPI, HTTPException, Query
import os
from pathlib import Path


app = FastAPI(title="Repo Reader", version=os.getenv("APP_VERSION", "0.1.0"))
BASE_PATH = Path(os.getenv("REPO_BASE_PATH", "/repo")).resolve()


def _safe_path(rel_path: str) -> Path:
    p = (BASE_PATH / rel_path).resolve()
    if BASE_PATH not in p.parents and p != BASE_PATH:
        raise HTTPException(status_code=400, detail="path escapes base path")
    return p


@app.get("/health")
def health():
    return {"status": "ok", "base_path": str(BASE_PATH)}


@app.get("/list")
def list_files(path: str = Query(".", description="relative directory path"), limit: int = 200):
    root = _safe_path(path)
    if not root.exists():
        raise HTTPException(status_code=404, detail="path not found")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="path is not a directory")

    files = []
    for f in root.rglob("*"):
        if f.is_file():
            files.append(str(f.relative_to(BASE_PATH)))
            if len(files) >= limit:
                break
    return {"count": len(files), "files": files}


@app.get("/read")
def read_file(path: str = Query(..., description="relative file path"), max_chars: int = 20000):
    target = _safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    content = target.read_text(encoding="utf-8", errors="replace")
    return {"path": path, "content": content[:max_chars]}


@app.get("/search")
def search(pattern: str, path: str = ".", limit: int = 200):
    root = _safe_path(path)
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail="path not found")

    matches = []
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if pattern in text:
            matches.append(str(f.relative_to(BASE_PATH)))
            if len(matches) >= limit:
                break
    return {"pattern": pattern, "count": len(matches), "files": matches}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
