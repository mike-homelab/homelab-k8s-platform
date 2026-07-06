import os
import tempfile
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

from pydantic import BaseModel

class SplitRequest(BaseModel):
    bucket_name: str
    object_name: str

def get_minio_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False
    )

@app.get("/healthz")
async def healthz():
    try:
        minio_client = get_minio_client()
        if not minio_client.bucket_exists("kito-processed-documents"):
            minio_client.make_bucket("kito-processed-documents")
    except Exception as e:
        logger.error(f"MinIO health check failed: {e}")
        return Response(status_code=500, content="MinIO degraded")
    return {"status": "healthy"}

@app.post("/split")
async def split_pdf(req: SplitRequest):
    bucket_name = req.bucket_name
    object_name = req.object_name
    logger.info(f"Splitting PDF from bucket {bucket_name}, object {object_name}")
    
    minio_client = get_minio_client()
    
    # Ensure processed bucket exists
    if not minio_client.bucket_exists("kito-processed-documents"):
        minio_client.make_bucket("kito-processed-documents")
        
    pages = []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, "input.pdf")
        
        # Download PDF from MinIO
        minio_client.fget_object(bucket_name, object_name, pdf_path)
        
        # Open PDF and split
        doc = fitz.open(pdf_path)
        for i, page in enumerate(doc):
            pix = page.get_pixmap()
            page_filename = f"{os.path.splitext(object_name)[0]}_page_{i}.png"
            page_path = os.path.join(tmpdir, page_filename)
            pix.save(page_path)
            
            # Upload to MinIO processed bucket
            minio_client.fput_object("kito-processed-documents", page_filename, page_path)
            pages.append(page_filename)
            logger.info(f"Uploaded page {i} as {page_filename}")
            
    return {"status": "success", "pages": pages, "count": len(pages)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
