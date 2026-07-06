import os
import json
import tempfile
import logging
import requests
import uuid
import base64
import time
import psycopg2
import fitz  # PyMuPDF
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, BackgroundTasks
from minio import Minio
from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot-service")

# ===== CONFIGURATION =====

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres.kito.svc:5432/kito")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio.monitoring.svc:9000").replace("http://", "").replace("https://", "")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

LITELLM_ENDPOINT = os.getenv("LITELLM_ENDPOINT", "http://litellm.ai-platform.svc:4000/v1")
LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-michael-homelab-llm-proxy")
BUILDER_VL_ENDPOINT = os.getenv("BUILDER_VL_ENDPOINT", "http://builder.ai-platform.svc:11434/v1")

# ===== CLIENTS =====

def get_minio_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False
    )

def get_db_conn():
    return psycopg2.connect(DATABASE_URL)

# ===== DATABASE =====

def init_db():
    """Create tables for event deduplication, job tracking, and page queue."""
    conn = get_db_conn()
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
                total_pages INTEGER DEFAULT 0,
                completed_pages INTEGER DEFAULT 0,
                error TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS page_tasks (
                id SERIAL PRIMARY KEY,
                job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
                page_index INTEGER,
                image_path TEXT,
                status TEXT DEFAULT 'pending',
                ast_content TEXT,
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
    conn = get_db_conn()
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


def create_job(job_id: str, channel_id: str, original_filename: str, format_type: str, total_pages: int = 0) -> str:
    """Create a new job record."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (id, channel_id, original_filename, status, format, total_pages) VALUES (%s, %s, %s, %s, %s, %s)",
                (job_id, channel_id, original_filename, "pending", format_type, total_pages)
            )
        conn.commit()
    finally:
        conn.close()
    return job_id


def update_job_status(job_id: str, status: str, error: str = None):
    """Update job status."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = %s, error = %s, updated_at = NOW() WHERE id = %s",
                (status, error, job_id)
            )
        conn.commit()
    finally:
        conn.close()


def enqueue_pages(job_id: str, page_image_paths: list):
    """Insert all page tasks into the queue."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            for i, image_path in enumerate(page_image_paths):
                cur.execute(
                    "INSERT INTO page_tasks (job_id, page_index, image_path, status) VALUES (%s, %s, %s, 'pending')",
                    (job_id, i, image_path)
                )
            cur.execute(
                "UPDATE jobs SET total_pages = %s, status = 'queued', updated_at = NOW() WHERE id = %s",
                (len(page_image_paths), job_id)
            )
        conn.commit()
    finally:
        conn.close()


def get_pending_pages(job_id: str) -> list:
    """Get all pending page tasks for a job, ordered by page index."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, page_index, image_path FROM page_tasks WHERE job_id = %s AND status = 'pending' ORDER BY page_index",
                (job_id,)
            )
            rows = cur.fetchall()
            return [{"id": r[0], "page_index": r[1], "image_path": r[2]} for r in rows]
    finally:
        conn.close()


def mark_page_completed(page_task_id: int, ast_content: str):
    """Mark a page task as completed with its AST content."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE page_tasks SET status = 'completed', ast_content = %s, updated_at = NOW() WHERE id = %s",
                (ast_content, page_task_id)
            )
            # Update job completed count
            cur.execute("""
                UPDATE jobs SET completed_pages = (
                    SELECT COUNT(*) FROM page_tasks WHERE job_id = (
                        SELECT job_id FROM page_tasks WHERE id = %s
                    ) AND status = 'completed'
                ), updated_at = NOW()
                WHERE id = (SELECT job_id FROM page_tasks WHERE id = %s)
            """, (page_task_id, page_task_id))
        conn.commit()
    finally:
        conn.close()


def mark_page_failed(page_task_id: int, error: str):
    """Mark a page task as failed."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE page_tasks SET status = 'failed', error = %s, updated_at = NOW() WHERE id = %s",
                (error, page_task_id)
            )
        conn.commit()
    finally:
        conn.close()


def get_completed_asts(job_id: str) -> list:
    """Retrieve all completed ASTs for a job, ordered by page index."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ast_content FROM page_tasks WHERE job_id = %s AND status = 'completed' ORDER BY page_index",
                (job_id,)
            )
            rows = cur.fetchall()
            return [json.loads(r[0]) for r in rows if r[0]]
    finally:
        conn.close()


# ===== APP LIFECYCLE =====

@asynccontextmanager
async def lifespan(app):
    """Initialize database on startup."""
    init_db()
    yield

app = FastAPI(title="Kito Slack Bot Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    try:
        conn = get_db_conn()
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


# ===== SLACK HELPERS =====

def download_slack_file(download_url: str, save_path: str):
    """Download a file from Slack using the bot token."""
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
    """Post a text message to a Slack channel."""
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


# ===== IN-PROCESS TOOLS (formerly separate microservices) =====

def split_pdf(pdf_path: str, doc_id: str) -> list:
    """Split a PDF into per-page PNG images and upload to MinIO.
    Returns list of MinIO object paths.
    Formerly: pdf-splitter microservice.
    """
    minio_client = get_minio_client()

    # Ensure processed bucket exists
    if not minio_client.bucket_exists("kito-processed-documents"):
        minio_client.make_bucket("kito-processed-documents")

    pages = []
    with tempfile.TemporaryDirectory() as tmpdir:
        doc = fitz.open(pdf_path)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=150)
            page_filename = f"{doc_id}_page_{i}.png"
            page_path = os.path.join(tmpdir, page_filename)
            pix.save(page_path)

            minio_client.fput_object("kito-processed-documents", page_filename, page_path)
            pages.append(page_filename)
            logger.info(f"Split and uploaded page {i} as {page_filename}")

    return pages


def build_page_ast(image_path: str) -> dict:
    """Call VLM to extract structured markdown from a page image.
    This is the ONLY external HTTP call in the pipeline (to Ollama VLM).
    Formerly: ast-builder microservice.
    Includes retry with backoff for resilience.
    """
    minio_client = get_minio_client()

    with tempfile.TemporaryDirectory() as tmpdir:
        local_image_path = os.path.join(tmpdir, "page.png")

        # Download page image from MinIO
        minio_client.fget_object("kito-processed-documents", image_path, local_image_path)

        # Base64 encode the image
        with open(local_image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")

        # Call VLM with retry
        headers = {
            "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "builder",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract all text and structure from this document page. Output clean markdown content with clear headers, lists, and tables. Preserve the original structure as closely as possible."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 32768
        }

        # Retry with exponential backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    f"{BUILDER_VL_ENDPOINT}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=300
                )

                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    return {
                        "page": image_path,
                        "content": content
                    }
                else:
                    logger.error(f"VLM call failed (attempt {attempt + 1}): {response.status_code} {response.text}")

            except requests.exceptions.RequestException as e:
                logger.error(f"VLM request error (attempt {attempt + 1}): {e}")

            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                logger.info(f"Retrying VLM call in {wait_time}s...")
                time.sleep(wait_time)

        raise Exception(f"VLM call failed after {max_retries} attempts for {image_path}")


def generate_document(pages_ast: list, format_type: str) -> str:
    """Generate a PDF or DOCX from AST data, upload to MinIO.
    Returns the MinIO object name.
    Formerly: artifact-generator microservice.
    """
    minio_client = get_minio_client()

    # Ensure generated bucket exists
    if not minio_client.bucket_exists("kito-generated-artifacts"):
        minio_client.make_bucket("kito-generated-artifacts")

    file_id = str(uuid.uuid4())
    filename = f"document_{file_id}.{format_type.lower()}"

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = os.path.join(tmpdir, filename)

        if format_type.lower() == "docx":
            doc = Document()
            doc.add_heading("Processed Document Output", 0)

            for page_ast in pages_ast:
                content = page_ast.get("content", "")
                for line in content.split("\n"):
                    if line.startswith("# "):
                        doc.add_heading(line[2:], level=1)
                    elif line.startswith("## "):
                        doc.add_heading(line[3:], level=2)
                    elif line.startswith("### "):
                        doc.add_heading(line[4:], level=3)
                    elif line.strip():
                        doc.add_paragraph(line)
                doc.add_page_break()

            doc.save(local_path)

        else:
            # Default to PDF generation using ReportLab
            doc = SimpleDocTemplate(local_path, pagesize=letter)
            styles = getSampleStyleSheet()

            normal_style = ParagraphStyle(
                'CustomNormal',
                parent=styles['Normal'],
                fontSize=10,
                leading=14,
                spaceAfter=6
            )
            h1_style = ParagraphStyle(
                'CustomH1',
                parent=styles['Heading1'],
                fontSize=18,
                leading=22,
                spaceAfter=12,
                keepWithNext=True
            )

            story = [Paragraph("Processed Document Output", styles['Title']), Spacer(1, 20)]

            for page_ast in pages_ast:
                content = page_ast.get("content", "")
                for line in content.split("\n"):
                    if line.startswith("# "):
                        story.append(Paragraph(line[2:], h1_style))
                    elif line.startswith("## "):
                        story.append(Paragraph(line[3:], styles['Heading2']))
                    elif line.startswith("### "):
                        story.append(Paragraph(line[4:], styles['Heading3']))
                    elif line.strip():
                        story.append(Paragraph(line, normal_style))
                story.append(Spacer(1, 15))

            doc.build(story)

        # Upload to MinIO
        minio_client.fput_object("kito-generated-artifacts", filename, local_path)
        logger.info(f"Generated and uploaded artifact: {filename}")

    return filename


# ===== AGENT TOOLS DEFINITION =====

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "process_scanned_document",
            "description": "Process a scanned PDF document that was uploaded by the user. Extracts text from scanned page images using a Vision Language Model and reconstructs the document in the requested output format (PDF or Word/DOCX). Use this tool when the user uploads a PDF file and wants it converted, reformatted, digitized, or processed in any way.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_format": {
                        "type": "string",
                        "enum": ["pdf", "docx"],
                        "description": "The desired output format. Use 'pdf' by default unless the user explicitly asks for Word, DOCX, or .doc format."
                    }
                },
                "required": ["output_format"]
            }
        }
    }
    # Future tools:
    # - search_internet: Search the web using SearXNG
    # - query_knowledge_base: RAG over indexed documents using perception model
    # - call_azure_agent: Delegate tasks to Azure cloud agents
]

SYSTEM_PROMPT = """/no_think
You are Kito, a personal AI assistant on Slack running on a homelab Kubernetes cluster.

Current capabilities:
- Answer questions and have conversations on any topic
- Process scanned PDF documents — extract text and reconstruct as clean PDF or Word/DOCX

If the user uploads a PDF file and wants it processed, converted, digitized, or reformatted, use the process_scanned_document tool.
If the user just wants to chat or ask a question, respond directly without using any tools.

Be helpful, concise, and friendly. You can handle complex questions with detailed answers."""


# ===== MAIN AGENT =====

def run_agent(channel_id: str, user_message: str, files_metadata: list):
    """Main agent loop: analyst LLM decides what to do based on message + file context."""
    try:
        # Build user content including file metadata so the LLM knows what was uploaded
        content_parts = []

        if files_metadata:
            file_descriptions = []
            for f in files_metadata:
                file_descriptions.append(
                    f"[Attached file: '{f['name']}', type: {f['filetype']}, size: {f['size']} bytes]"
                )
            content_parts.append("\n".join(file_descriptions))

        if user_message:
            # Strip bot mention tags like <@U0BFA1LL9DJ>
            clean_message = user_message
            import re
            clean_message = re.sub(r'<@[A-Z0-9]+>', '', clean_message).strip()
            if clean_message:
                content_parts.append(clean_message)

        content = "\n\n".join(content_parts)
        if not content.strip():
            return

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content}
        ]

        # Call analyst LLM with tools
        headers = {
            "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "analyst",
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "max_tokens": 4096
        }

        response = requests.post(
            f"{LITELLM_ENDPOINT}/chat/completions",
            json=payload,
            headers=headers,
            timeout=120
        )

        if response.status_code != 200:
            logger.error(f"Agent LLM call failed: {response.status_code} {response.text}")
            post_message_to_slack(channel_id, "I'm having trouble thinking right now. Please try again.")
            return

        result = response.json()
        choice = result["choices"][0]
        message = choice["message"]

        # Check if LLM wants to call a tool
        tool_calls = message.get("tool_calls")

        if tool_calls:
            for tool_call in tool_calls:
                fn_name = tool_call["function"]["name"]
                fn_args_str = tool_call["function"].get("arguments", "{}")

                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                if fn_name == "process_scanned_document":
                    pdf_file = next((f for f in files_metadata if f.get("filetype") == "pdf"), None)
                    if pdf_file:
                        output_format = fn_args.get("output_format", "pdf")
                        post_message_to_slack(
                            channel_id,
                            f"📄 Processing '{pdf_file['name']}' → {output_format.upper()}. Starting pipeline..."
                        )
                        process_document_pipeline(
                            pdf_file["url_private_download"],
                            pdf_file["name"],
                            channel_id,
                            output_format
                        )
                    else:
                        post_message_to_slack(
                            channel_id,
                            "I'd like to process a document, but no PDF file was attached. Please upload a PDF."
                        )

                # Future tool handlers:
                # elif fn_name == "search_internet": ...
                # elif fn_name == "query_knowledge_base": ...

                else:
                    logger.warning(f"Unknown tool call: {fn_name}")
                    post_message_to_slack(channel_id, f"I tried to use a capability I don't have yet: {fn_name}")

        else:
            # No tool call — LLM responded directly (conversation mode)
            reply = message.get("content", "").strip()
            if reply:
                post_message_to_slack(channel_id, reply)

    except Exception as e:
        logger.exception("Agent execution failed")
        post_message_to_slack(channel_id, f"Sorry, something went wrong: {str(e)}")


# ===== DOCUMENT PROCESSING PIPELINE (with page queue) =====

def process_document_pipeline(download_url: str, original_filename: str, channel_id: str, format_type: str):
    """Full document processing pipeline using PostgreSQL page queue.

    Flow:
    1. Download PDF from Slack → MinIO
    2. Split PDF into per-page images (in-process)
    3. Enqueue all pages into PostgreSQL page_tasks
    4. Process pages one-by-one, calling VLM for each
    5. Collect completed ASTs
    6. Generate final document (in-process)
    7. Upload result to Slack
    """
    job_id = str(uuid.uuid4())
    create_job(job_id, channel_id, original_filename, format_type)

    try:
        minio_client = get_minio_client()

        doc_id = str(uuid.uuid4())
        file_ext = os.path.splitext(original_filename)[1] or ".pdf"
        raw_filename = f"{doc_id}{file_ext}"

        with tempfile.TemporaryDirectory() as tmpdir:
            local_pdf_path = os.path.join(tmpdir, "raw.pdf")

            # 1. Download from Slack
            update_job_status(job_id, "downloading")
            logger.info(f"[Job {job_id}] Downloading from Slack...")
            download_slack_file(download_url, local_pdf_path)

            # Upload raw file to MinIO for archival
            minio_client.fput_object("kito-raw-documents", raw_filename, local_pdf_path)

            # 2. Split PDF into pages (in-process)
            update_job_status(job_id, "splitting")
            logger.info(f"[Job {job_id}] Splitting PDF...")
            page_image_paths = split_pdf(local_pdf_path, doc_id)

            if not page_image_paths:
                raise Exception("No pages extracted from PDF")

            total_pages = len(page_image_paths)
            post_message_to_slack(channel_id, f"📑 Split into {total_pages} pages. Starting text extraction...")

            # 3. Enqueue all pages
            enqueue_pages(job_id, page_image_paths)

            # 4. Process pages one-by-one from the queue
            update_job_status(job_id, "building_ast")
            completed = 0
            failed = 0
            pending_pages = get_pending_pages(job_id)

            for page_task in pending_pages:
                try:
                    logger.info(f"[Job {job_id}] Processing page {page_task['page_index'] + 1}/{total_pages}...")
                    ast = build_page_ast(page_task["image_path"])
                    mark_page_completed(page_task["id"], json.dumps(ast))
                    completed += 1

                    # Progress update every 10 pages or at the last page
                    if completed % 10 == 0 or completed == total_pages:
                        post_message_to_slack(
                            channel_id,
                            f"⏳ Progress: {completed}/{total_pages} pages extracted ({failed} failed)"
                        )

                except Exception as e:
                    mark_page_failed(page_task["id"], str(e))
                    failed += 1
                    logger.error(f"[Job {job_id}] Page {page_task['page_index']} failed: {e}")

            # 5. Collect completed ASTs
            pages_ast = get_completed_asts(job_id)

            if not pages_ast:
                raise Exception("No pages were successfully processed")

            if failed > 0:
                post_message_to_slack(
                    channel_id,
                    f"⚠️ {failed}/{total_pages} pages failed. Generating document from {len(pages_ast)} successful pages..."
                )

            # 6. Generate final document (in-process)
            update_job_status(job_id, "generating")
            logger.info(f"[Job {job_id}] Generating {format_type.upper()} document...")
            object_name = generate_document(pages_ast, format_type)

            # 7. Download from MinIO and upload to Slack
            update_job_status(job_id, "uploading")
            local_out_path = os.path.join(tmpdir, object_name)
            minio_client.fget_object("kito-generated-artifacts", object_name, local_out_path)

            comment = f"✅ Processed '{original_filename}' → {format_type.upper()} ({len(pages_ast)}/{total_pages} pages)"
            upload_file_to_slack(local_out_path, channel_id, object_name, comment)

        update_job_status(job_id, "completed")
        logger.info(f"[Job {job_id}] Pipeline completed successfully")

    except Exception as e:
        logger.exception(f"[Job {job_id}] Pipeline failed")
        update_job_status(job_id, "failed", error=str(e))
        post_message_to_slack(channel_id, f"❌ Processing failed: {str(e)}")


# ===== SLACK EVENT HANDLER =====

@app.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    logger.info(f"Received event type: {body.get('type')}")

    # Slack URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # Deduplicate events — Slack retries up to 3 times if no 200 within 3s
    event_id = body.get("event_id")
    if event_id and is_duplicate_event(event_id):
        logger.info(f"Duplicate event {event_id}, skipping")
        return {"status": "duplicate_skipped"}

    event = body.get("event", {})

    # Process user messages (not bot messages, not subtypes like edits/deletes)
    if event.get("type") == "message" and not event.get("bot_id") and not event.get("subtype"):
        channel_id = event.get("channel")
        user_message = event.get("text", "")
        files = event.get("files", [])

        # Extract file metadata for the agent
        files_metadata = [
            {
                "name": f.get("name", "unknown"),
                "filetype": f.get("filetype", "unknown"),
                "size": f.get("size", 0),
                "url_private_download": f.get("url_private_download")
            }
            for f in files
        ]

        # Let the agent decide what to do
        background_tasks.add_task(run_agent, channel_id, user_message, files_metadata)

    return {"status": "event_processed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
