import os
import logging
from fastapi import FastAPI, Response
import fitz  # PyMuPDF
from minio import Minio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf-splitter")

app = FastAPI(title="Kito PDF Splitter")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio.monitoring.svc:9000").replace("http://", "").replace("https://", "")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")

@app.get("/healthz")
async def healthz():
    try:
        minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=False
        )
        if not minio_client.bucket_exists("kito-processed-documents"):
            minio_client.make_bucket("kito-processed-documents")
    except Exception as e:
        logger.error(f"MinIO health check failed: {e}")
        return Response(status_code=500, content="MinIO degraded")
    return {"status": "healthy"}

@app.post("/split")
async def split_pdf(bucket_name: str, object_name: str):
    logger.info(f"Splitting PDF from bucket {bucket_name}, object {object_name}")
    # PDF splitting logic goes here
    return {"status": "success", "pages": 0}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
