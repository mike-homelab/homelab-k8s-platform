import os
import tempfile
import logging
import requests
import uuid
import psycopg2
from fastapi import FastAPI, Request, Response
from minio import Minio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot-service")

app = FastAPI(title="Kito Slack Bot Service")

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
    url = "https://slack.com/api/files.upload"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    with open(file_path, "rb") as f:
        files = {
            "file": (filename, f, "application/octet-stream")
        }
        payload = {
            "channels": channel_id,
            "initial_comment": comment
        }
        response = requests.post(url, headers=headers, data=payload, files=files, timeout=120)
        logger.info(f"Slack upload response: {response.text}")

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

def process_pipeline(download_url: str, original_filename: str, channel_id: str, format_type: str):
    try:
        minio_client = get_minio_client()
        
        # Unique document ID
        doc_id = str(uuid.uuid4())
        file_ext = os.path.splitext(original_filename)[1] or ".pdf"
        raw_filename = f"{doc_id}{file_ext}"
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_pdf_path = os.path.join(tmpdir, "raw.pdf")
            
            # 1. Download file from Slack
            logger.info("Downloading file from Slack...")
            download_slack_file(download_url, local_pdf_path)
            
            # 2. Upload raw file to MinIO
            logger.info("Uploading raw file to MinIO...")
            minio_client.fput_object("kito-raw-documents", raw_filename, local_pdf_path)
            
            # 3. Call pdf-splitter
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
            logger.info("Uploading file back to Slack...")
            comment = f"Processed document '{original_filename}'. Format: {format_type.upper()}"
            upload_file_to_slack(local_out_path, channel_id, object_name, comment)
            
    except Exception as e:
        logger.exception("Pipeline execution failed")
        post_message_to_slack(channel_id, f"Sorry, processing failed: {str(e)}")

@app.post("/slack/events")
async def slack_events(request: Request):
    body = await request.json()
    logger.info(f"Received event: {body}")
    
    # Slack URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}
        
    event = body.get("event", {})
    event_type = event.get("type")
    
    # Process message with files
    if event_type == "message" and not event.get("bot_id"):
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
            
            # Process in background (simplified here for execution sync)
            process_pipeline(download_url, original_filename, channel_id, format_type)
            
    elif event_type == "file_shared":
        file_id = event.get("file_id")
        channel_id = event.get("channel_id")
        
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        info_resp = requests.get(f"https://slack.com/api/files.info?file={file_id}", headers=headers, timeout=15)
        logger.info(f"files.info response: {info_resp.text}")
        
        if info_resp.status_code == 200 and info_resp.json().get("ok"):
            file_data = info_resp.json().get("file", {})
            # Only process if it is a PDF
            if file_data.get("filetype") == "pdf":
                download_url = file_data.get("url_private_download")
                original_filename = file_data.get("name", "document.pdf")
                format_type = "pdf" # Default to pdf for simple uploads
                
                post_message_to_slack(channel_id, f"Received file '{original_filename}'. Processing pipeline started...")
                process_pipeline(download_url, original_filename, channel_id, format_type)
            
    return {"status": "event_processed"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
