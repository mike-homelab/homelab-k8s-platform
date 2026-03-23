import os
import subprocess
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router as api_router
from .tools import GITHUB_PAT, GITHUB_USERNAME, REPO_OWNER, REPO_NAME, WORKSPACE_DIR

app = FastAPI(title="Jevin Agent API", version="1.0.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# On boot, clone the repository into the ephemeral volume
if GITHUB_PAT and GITHUB_USERNAME:
    if not os.path.exists(os.path.join(WORKSPACE_DIR, ".git")):
        print(f"[*] Cloning repository to {WORKSPACE_DIR}")
        repo_url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}.git"
        try:
            subprocess.run(["git", "clone", repo_url, WORKSPACE_DIR], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            # Mask the PAT in the output for security
            safe_url = repo_url.replace(GITHUB_PAT, "***")
            stderr_msg = e.stderr.replace(GITHUB_PAT, "***") if e.stderr else "No stderr returned."
            raise RuntimeError(f"Git clone failed for {safe_url}. STDERR: {stderr_msg}")
else:
    print("[!] Warning: GITHUB_PAT or GITHUB_USERNAME is missing. GitOps features may fail.")

app.include_router(api_router)

@app.get("/health")
async def health():
    return {"status": "ok"}
