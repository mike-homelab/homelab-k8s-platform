import os
import logging
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
import psycopg2
from minio import Minio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot-service")

app = FastAPI(title="Kito Slack Bot Service")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres.kito.svc:5432/kito")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio.monitoring.svc:9000").replace("http://", "").replace("https://", "")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")

@app.get("/healthz")
async def healthz():
    try:
        # Check Postgres connection
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
    except Exception as e:
        logger.error(f"Postgres health check failed: {e}")
        return Response(status_code=500, content="Postgres degraded")

    try:
        # Check MinIO connection
        minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=False
        )
        if not minio_client.bucket_exists("kito-raw-documents"):
            minio_client.make_bucket("kito-raw-documents")
    except Exception as e:
        logger.error(f"MinIO health check failed: {e}")
        return Response(status_code=500, content="MinIO degraded")

    return {"status": "healthy"}

@app.post("/slack/events")
async def slack_events(request: Request):
    body = await request.json()
    logger.info(f"Received event: {body}")
    
    # Slack URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}
        
    return {"status": "event_processed"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
