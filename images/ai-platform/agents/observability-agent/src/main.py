from fastapi import FastAPI, HTTPException
import os

from observability_clients import query_prometheus, query_loki

app = FastAPI(
    title="Observability Agent",
    version=os.getenv("APP_VERSION", "0.0.0"),
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return {"ready": True}


@app.get("/metrics/query")
def metrics_query(q: str):
    try:
        return query_prometheus(q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/logs/query")
def logs_query(q: str, limit: int = 100):
    try:
        return query_loki(q, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info",
    )
