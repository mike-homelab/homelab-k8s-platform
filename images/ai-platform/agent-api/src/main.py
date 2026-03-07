from fastapi import FastAPI

app = FastAPI(title="agent-api", version="0.1.0")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "agent-api", "status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    return {"status": "ready"}
