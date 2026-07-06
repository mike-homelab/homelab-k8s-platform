import os
import tempfile
import logging
import requests
import uuid
import psycopg2
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, BackgroundTasks
from minio import Minio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot-service")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres.kito.svc:5432/kito")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio.monitoring.svc:9000").replace("http://", "").replace("https://", "")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

LITELLM_ENDPOINT = os.getenv("LITELLM_ENDPOINT", "http://litellm.ai-platform.svc:4000/v1")
LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-michael-homelab-llm-proxy")

SPLITTER_ENDPOINT = os.getenv("SPLITTER_ENDPOINT", "http://kito-pdf-splitter.kito.svc:5000")
AST_BUILDER_ENDPOINT = os.getenv("AST_BUILDER_ENDPOINT", "http://kito-ast-builder.kito.svc:5001")
GENERATOR_ENDPOINT = os.getenv("GENERATOR_ENDPOINT", "http://kito-artifact-generator.kito.svc:5002")

def get_minio_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False
    )

def init_db():
    """Create tables for event deduplication and job tracking."""
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                channel_id TEXT,
                original_filename TEXT,
                status TEXT DEFAULT 'pending',
                format TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
    conn.commit()
    conn.close()
    logger.info("Database tables initialized")

def is_duplicate_event(event_id: str) -> bool:
    """Check if an event has already been processed. Insert if not."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM processed_events WHERE event_id = %s", (event_id,))
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute("INSERT INTO processed_events (event_id) VALUES (%s)", (event_id,))
        conn.commit()
        return exists
    finally:
        conn.close()

def update_job_status(job_id: str, status: str, error: str = None):
    """Update job status in the database."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = %s, error = %s, updated_at = NOW() WHERE id = %s",
                (status, error, job_id)
            )
        conn.commit()
    finally:
        conn.close()

def create_job(job_id: str, channel_id: str, original_filename: str, format_type: str):
    """Create a new job record in the database."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (id, channel_id, original_filename, status, format) VALUES (%s, %s, %s, %s, %s)",
                (job_id, channel_id, original_filename, "pending", format_type)
            )
        conn.commit()
    finally:
        conn.close()

@asynccontextmanager
async def lifespan(app):
    """Initialize database on startup."""
    init_db()
    yield

app = FastAPI(title="Kito Slack Bot Service", lifespan=lifespan)

@app.get("/healthz")
async def healthz():
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
    except Exception as e:
        logger.error(f"Postgres health check failed: {e}")
        return Response(status_code=500, content="Postgres degraded")

    try:
        minio_client = get_minio_client()
        if not minio_client.bucket_exists("kito-raw-documents"):
            minio_client.make_bucket("kito-raw-documents")
    except Exception as e:
        logger.error(f"MinIO health check failed: {e}")
        return Response(status_code=500, content="MinIO degraded")

    return {"status": "healthy"}

def classify_format_request(user_message: str) -> str:
    """Use LiteLLM analyst model to decide if the user wants a pdf or docx."""
    try:
        headers = {
            "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "analyst",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a format classifier helper. Classify the user's message to determine if they want a PDF document or a Word/DOCX document. Reply with only one word: either 'pdf' or 'docx'. If they do not specify or if it's ambiguous, default to 'pdf'."
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            "max_tokens": 10
        }
        response = requests.post(f"{LITELLM_ENDPOINT}/chat/completions", json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            choice = response.json()["choices"][0]["message"]["content"].strip().lower()
            if "docx" in choice or "word" in choice:
                return "docx"
        return "pdf"
    except Exception as e:
        logger.error(f"LLM classification failed, defaulting to pdf: {e}")
        return "pdf"

def download_slack_file(download_url: str, save_path: str):
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    response = requests.get(download_url, headers=headers, stream=True, timeout=60)
    response.raise_for_status()
    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

def upload_file_to_slack(file_path: str, channel_id: str, filename: str, comment: str):
    """Upload file to Slack using the new 3-step API (files.upload is deprecated)."""
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    file_size = os.path.getsize(file_path)

    # Step 1: Get an external upload URL from Slack
    get_url_resp = requests.get(
        "https://slack.com/api/files.getUploadURLExternal",
        headers=headers,
        params={"filename": filename, "length": file_size},
        timeout=15
    )
    get_url_resp.raise_for_status()
    url_data = get_url_resp.json()
    if not url_data.get("ok"):
        raise Exception(f"getUploadURLExternal failed: {url_data}")
    upload_url = url_data["upload_url"]
    file_id = url_data["file_id"]

    # Step 2: Upload the file bytes to the provided URL
    with open(file_path, "rb") as f:
        upload_resp = requests.post(upload_url, data=f, timeout=120)
    if upload_resp.status_code != 200:
        raise Exception(f"File upload to Slack URL failed: {upload_resp.status_code} {upload_resp.text}")

    # Step 3: Complete the upload and share to the channel
    complete_resp = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={**headers, "Content-Type": "application/json"},
        json={"files": [{"id": file_id}], "channel_id": channel_id, "initial_comment": comment},
        timeout=15
    )
    complete_resp.raise_for_status()
    result = complete_resp.json()
    if not result.get("ok"):
        raise Exception(f"completeUploadExternal failed: {result}")
    logger.info(f"Slack upload completed: file_id={file_id}")

def post_message_to_slack(channel_id: str, text: str):
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": channel_id,
        "text": text
    }
    requests.post(url, headers=headers, json=payload, timeout=10)

def process_simple_message(channel_id: str, text: str):
    try:
        headers = {
            "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "analyst",
            "messages": [
                {"role": "system", "content": "You are Kito, a helpful and friendly AI assistant. You process text and answer questions."},
                {"role": "user", "content": text}
            ],
            "max_tokens": 2048
        }
        response = requests.post(f"{LITELLM_ENDPOINT}/chat/completions", json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            reply = response.json()["choices"][0]["message"]["content"].strip()
            post_message_to_slack(channel_id, reply)
        else:
            logger.error(f"LLM simple message failed: {response.status_code} {response.text}")
            post_message_to_slack(channel_id, "I'm having trouble thinking right now.")
    except Exception as e:
        logger.error(f"Simple message error: {e}")

def process_pipeline(download_url: str, original_filename: str, channel_id: str, format_type: str):
    # Create a tracked job
    job_id = str(uuid.uuid4())
    create_job(job_id, channel_id, original_filename, format_type)

    try:
        minio_client = get_minio_client()
        
        # Unique document ID
        doc_id = str(uuid.uuid4())
        file_ext = os.path.splitext(original_filename)[1] or ".pdf"
        raw_filename = f"{doc_id}{file_ext}"
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_pdf_path = os.path.join(tmpdir, "raw.pdf")
            
            # 1. Download file from Slack
            update_job_status(job_id, "downloading")
            logger.info("Downloading file from Slack...")
            download_slack_file(download_url, local_pdf_path)
            
            # 2. Upload raw file to MinIO
            logger.info("Uploading raw file to MinIO...")
            minio_client.fput_object("kito-raw-documents", raw_filename, local_pdf_path)
            
            # 3. Call pdf-splitter
            update_job_status(job_id, "splitting")
            logger.info("Triggering pdf-splitter...")
            split_response = requests.post(
                f"{SPLITTER_ENDPOINT}/split",
                json={"bucket_name": "kito-raw-documents", "object_name": raw_filename},
                timeout=120
            )
            split_response.raise_for_status()
            pages = split_response.json().get("pages", [])
            
            if not pages:
                raise Exception("No pages returned by pdf-splitter")
                
            # 4. Call ast-builder for each page
            update_job_status(job_id, "building_ast")
            logger.info("Triggering ast-builder for each page...")
            pages_ast = []
            for page in pages:
                ast_response = requests.post(
                    f"{AST_BUILDER_ENDPOINT}/build",
                    params={"image_path": page},
                    timeout=180
                )
                ast_response.raise_for_status()
                pages_ast.append(ast_response.json().get("ast", {}))
                
            # 5. Call artifact-generator
            update_job_status(job_id, "generating")
            logger.info("Triggering artifact-generator...")
            gen_response = requests.post(
                f"{GENERATOR_ENDPOINT}/generate",
                json={"ast_data": {"pages": pages_ast}, "format": format_type},
                timeout=120
            )
            gen_response.raise_for_status()
            object_name = gen_response.json().get("object_name")
            
            # 6. Download the generated artifact from MinIO
            logger.info("Downloading generated artifact from MinIO...")
            local_out_path = os.path.join(tmpdir, object_name)
            minio_client.fget_object("kito-generated-artifacts", object_name, local_out_path)
            
            # 7. Upload final document to Slack
            update_job_status(job_id, "uploading")
            logger.info("Uploading file back to Slack...")
            comment = f"Processed document '{original_filename}'. Format: {format_type.upper()}"
            upload_file_to_slack(local_out_path, channel_id, object_name, comment)
            
        update_job_status(job_id, "completed")
            
    except Exception as e:
        logger.exception("Pipeline execution failed")
        update_job_status(job_id, "failed", error=str(e))
        post_message_to_slack(channel_id, f"Sorry, processing failed: {str(e)}")

@app.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    logger.info(f"Received event: {body}")
    
    # Slack URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # Deduplicate events — Slack retries up to 3 times if no 200 within 3s
    event_id = body.get("event_id")
    if event_id and is_duplicate_event(event_id):
        logger.info(f"Duplicate event {event_id}, skipping")
        return {"status": "duplicate_skipped"}

    event = body.get("event", {})
    event_type = event.get("type")
    
    # Process message events (covers both text-only and file uploads)
    if event_type == "message" and not event.get("bot_id") and not event.get("subtype"):
        files = event.get("files", [])
        channel_id = event.get("channel")
        user_message = event.get("text", "")
        
        # Find first PDF file
        pdf_file = next((f for f in files if f.get("filetype") == "pdf"), None)
        if pdf_file:
            download_url = pdf_file.get("url_private_download")
            original_filename = pdf_file.get("name", "document.pdf")
            
            # Acknowledge receipt
            post_message_to_slack(channel_id, f"Received '{original_filename}'. Analyzing format request...")
            
            # Classify format
            format_type = classify_format_request(user_message)
            post_message_to_slack(channel_id, f"Formatting output as: {format_type.upper()}. Processing pipeline started...")
            
            # Process in background
            background_tasks.add_task(process_pipeline, download_url, original_filename, channel_id, format_type)
            
        elif user_message.strip():
            background_tasks.add_task(process_simple_message, channel_id, user_message)
            
    return {"status": "event_processed"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
