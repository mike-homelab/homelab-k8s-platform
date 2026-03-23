import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .config import _ensure_otel
from .routes.chat import router as chat_router
from .routes.ops import router as ops_router

app = FastAPI(title="agent-api", version="0.14.0")

cors_allow_origins = [x.strip() for x in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins if cors_allow_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_ensure_otel(app)
Instrumentator().instrument(app).expose(app, include_in_schema=False, endpoint="/metrics")

@app.get("/")
def root() -> dict[str, str]:
    return {"service": "agent-api", "status": "ok"}

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "healthy"}

@app.get("/readyz")
def readyz() -> dict[str, str]:
    return {"status": "ready"}

app.include_router(chat_router)
app.include_router(ops_router)
