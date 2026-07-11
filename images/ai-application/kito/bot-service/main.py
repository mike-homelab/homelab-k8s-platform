import os
import re
import io
import json
import tempfile
import logging
import requests
import uuid
import base64
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse
import psycopg2
import fitz  # PyMuPDF — used for page count and archival
import pypandoc
from pdf2docx import Converter as Pdf2DocxConverter
from PIL import Image
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, BackgroundTasks
from minio import Minio
from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from langfuse import observe

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
# Docling sidecar runs in the builder pod (GPU-accelerated, port 8100)
DOCLING_SERVICE_URL = os.getenv("DOCLING_SERVICE_URL", "http://builder.ai-platform.svc:8100")
# Azure Document Intelligence — for table recovery on scanned PDFs
AZURE_DI_ENDPOINT = os.getenv("AZURE_DI_ENDPOINT", "")
AZURE_DI_KEY = os.getenv("AZURE_DI_KEY", "")


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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_confirmations (
                id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                download_url TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                pdf_type TEXT DEFAULT 'scanned',
                total_pages INTEGER DEFAULT 0,
                digital_pages INTEGER DEFAULT 0,
                scanned_pages INTEGER DEFAULT 0,
                format_type TEXT DEFAULT 'docx',
                message_ts TEXT,
                created_at TIMESTAMP DEFAULT NOW()
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



# ===== REFERENCE DOCUMENT FOR PANDOC =====

REFERENCE_DOC_PATH = "/tmp/kito_reference.docx"


def create_reference_doc():
    """Generate a professional reference document for pandoc DOCX output.
    This defines the styles that pandoc will use when converting markdown to DOCX.
    """
    doc = Document()

    # ----- Normal style -----
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(11)
    font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    pf = style.paragraph_format
    pf.space_after = Pt(6)
    pf.line_spacing = 1.15

    # ----- Heading 1 -----
    style = doc.styles['Heading 1']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(22)
    font.bold = True
    font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    pf = style.paragraph_format
    pf.space_before = Pt(24)
    pf.space_after = Pt(8)

    # ----- Heading 2 -----
    style = doc.styles['Heading 2']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(17)
    font.bold = True
    font.color.rgb = RGBColor(0x2E, 0x75, 0xB6)
    pf = style.paragraph_format
    pf.space_before = Pt(18)
    pf.space_after = Pt(6)

    # ----- Heading 3 -----
    style = doc.styles['Heading 3']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(14)
    font.bold = True
    font.color.rgb = RGBColor(0x44, 0x72, 0xC4)
    pf = style.paragraph_format
    pf.space_before = Pt(12)
    pf.space_after = Pt(4)

    # ----- Title -----
    style = doc.styles['Title']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(28)
    font.bold = True
    font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

    # Set default page margins
    for section in doc.sections:
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(2.54)
        section.right_margin = Cm(2.54)

    doc.save(REFERENCE_DOC_PATH)
    logger.info(f"Reference document created at {REFERENCE_DOC_PATH}")



# ===== DATA CLEANUP (24-hour retention) =====

RETENTION_HOURS = 24
CLEANUP_INTERVAL_SECONDS = 3600  # Run every hour


def cleanup_old_data():
    """Delete PostgreSQL rows and MinIO objects older than RETENTION_HOURS."""
    logger.info(f"Running cleanup: removing data older than {RETENTION_HOURS} hours...")

    # 1. Cleanup PostgreSQL
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            # Delete old page_tasks (child rows first due to FK)
            cur.execute(
                "DELETE FROM page_tasks WHERE created_at < NOW() - INTERVAL '%s hours'",
                (RETENTION_HOURS,)
            )
            pages_deleted = cur.rowcount

            # Delete old jobs
            cur.execute(
                "DELETE FROM jobs WHERE created_at < NOW() - INTERVAL '%s hours'",
                (RETENTION_HOURS,)
            )
            jobs_deleted = cur.rowcount

            # Delete old processed events
            cur.execute(
                "DELETE FROM processed_events WHERE created_at < NOW() - INTERVAL '%s hours'",
                (RETENTION_HOURS,)
            )
            events_deleted = cur.rowcount

            # Delete old pending confirmations (user never clicked a button)
            cur.execute(
                "DELETE FROM pending_confirmations WHERE created_at < NOW() - INTERVAL '%s hours'",
                (RETENTION_HOURS,)
            )
            confirmations_deleted = cur.rowcount

        conn.commit()
        conn.close()
        logger.info(
            f"Cleanup DB: {events_deleted} events, {jobs_deleted} jobs, "
            f"{pages_deleted} page_tasks, {confirmations_deleted} confirmations deleted"
        )
    except Exception as e:
        logger.error(f"Cleanup DB failed: {e}")

    # 2. Cleanup MinIO buckets
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RETENTION_HOURS)

    buckets_to_clean = [
        "kito-raw-documents",
        "kito-processed-documents",
        "kito-generated-artifacts"
    ]

    try:
        minio_client = get_minio_client()
        for bucket in buckets_to_clean:
            if not minio_client.bucket_exists(bucket):
                continue

            objects_to_delete = []
            for obj in minio_client.list_objects(bucket):
                if obj.last_modified and obj.last_modified < cutoff:
                    objects_to_delete.append(obj.object_name)

            for obj_name in objects_to_delete:
                minio_client.remove_object(bucket, obj_name)

            if objects_to_delete:
                logger.info(f"Cleanup MinIO: {len(objects_to_delete)} objects deleted from {bucket}")

    except Exception as e:
        logger.error(f"Cleanup MinIO failed: {e}")

    logger.info("Cleanup completed")


def _cleanup_scheduler():
    """Background thread: runs cleanup_old_data every CLEANUP_INTERVAL_SECONDS."""
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            cleanup_old_data()
        except Exception as e:
            logger.error(f"Cleanup scheduler error: {e}")


# ===== APP LIFECYCLE =====

@asynccontextmanager
async def lifespan(app):
    """Initialize database, reference document, and cleanup scheduler on startup."""
    init_db()
    create_reference_doc()

    # Start background cleanup thread (daemon=True so it dies with the process)
    cleanup_thread = threading.Thread(target=_cleanup_scheduler, daemon=True)
    cleanup_thread.start()
    logger.info(f"Cleanup scheduler started: every {CLEANUP_INTERVAL_SECONDS}s, retention {RETENTION_HOURS}h")

    # Run cleanup once at startup to clear any stale data
    threading.Thread(target=cleanup_old_data, daemon=True).start()

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


def post_interactive_message(channel_id: str, text: str, blocks: list) -> str:
    """Post a Slack message with Block Kit interactive elements (buttons).

    Returns the message timestamp (ts) for later updates.
    """
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": channel_id,
        "text": text,
        "blocks": blocks
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    data = resp.json()
    if data.get("ok"):
        return data.get("ts", "")
    logger.error(f"Interactive message failed: {data}")
    return ""


def update_slack_message(channel_id: str, message_ts: str, text: str, blocks: list = None):
    """Update an existing Slack message (e.g., to remove buttons after click)."""
    url = "https://slack.com/api/chat.update"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": channel_id,
        "ts": message_ts,
        "text": text,
    }
    if blocks is not None:
        payload["blocks"] = blocks
    requests.post(url, headers=headers, json=payload, timeout=10)


# ===== PDF TYPE DETECTION =====

def detect_pdf_type(pdf_path: str) -> dict:
    """Analyze PDF to determine if it's digital (has text layer) or scanned.

    Strategy: Check every page using PyMuPDF text extraction.
    A page is "digital" if it has >50 chars of extractable text.
    The document is classified by majority vote across all pages.

    Returns: {
        "type": "digital" | "scanned",
        "total_pages": int,
        "digital_pages": int,
        "scanned_pages": int,
        "confidence": float  # ratio of majority type
    }
    """
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    digital_count = 0
    scanned_count = 0

    for page in doc:
        text = page.get_text().strip()
        if len(text) > 50:
            digital_count += 1
        else:
            scanned_count += 1

    doc.close()

    if digital_count >= scanned_count:
        pdf_type = "digital"
        confidence = digital_count / total_pages if total_pages > 0 else 0
    else:
        pdf_type = "scanned"
        confidence = scanned_count / total_pages if total_pages > 0 else 0

    result = {
        "type": pdf_type,
        "total_pages": total_pages,
        "digital_pages": digital_count,
        "scanned_pages": scanned_count,
        "confidence": round(confidence, 2)
    }
    logger.info(f"PDF type detection: {result}")
    return result


# ===== DIRECT DIGITAL PDF CONVERSION =====

def convert_digital_pdf(pdf_path: str) -> str:
    """Direct PDF→DOCX conversion for digital PDFs using pdf2docx.

    Preserves original formatting: fonts, tables, images, layout.
    Returns the MinIO object name of the generated DOCX.
    """
    minio_client = get_minio_client()

    if not minio_client.bucket_exists("kito-generated-artifacts"):
        minio_client.make_bucket("kito-generated-artifacts")

    file_id = str(uuid.uuid4())
    filename = f"document_{file_id}.docx"

    with tempfile.TemporaryDirectory() as tmpdir:
        local_docx = os.path.join(tmpdir, filename)

        logger.info(f"pdf2docx: converting {pdf_path} → {local_docx}")
        cv = Pdf2DocxConverter(pdf_path)
        cv.convert(local_docx)
        cv.close()
        logger.info(f"pdf2docx: conversion complete → {local_docx}")

        minio_client.fput_object("kito-generated-artifacts", filename, local_docx)
        logger.info(f"Uploaded digital artifact: {filename}")

    return filename


# ===== LLM MARKDOWN VALIDATION (Sliding Window) =====

LLM_VALIDATION_PROMPT = """/no_think
You are a document formatting validator. You receive markdown text extracted from
a scanned PDF via OCR. Your job is to fix formatting issues WITHOUT changing the
actual content meaning.

You are given THREE sections:
- PREVIOUS PAGE: context from the page before (read-only, do NOT output this)
- CURRENT PAGE: the page you must validate and fix (output ONLY this page corrected)
- NEXT PAGE: context from the page after (read-only, do NOT output this)

Use the previous and next pages to understand context — for example, if a word
is split at a page boundary, you can see how it continues on the next page.

Fix these issues on the CURRENT PAGE:
1. Split words: words broken across lines with hyphens (e.g., "docu-\\nment" → "document")
2. OCR artifacts: common misreads like "rn"→"m", "l"→"1", "0"→"O" where context makes it clear
3. Broken tables: fix misaligned columns, add missing separator rows
4. Excessive whitespace: collapse multiple blank lines
5. Heading hierarchy: ensure consistent markdown heading levels
6. Garbled characters: fix encoding issues or character corruption

Do NOT:
- Change the meaning of any text
- Add information that isn't there
- Remove tables, figures, or structural elements
- Rewrite or rephrase sentences
- Output the previous or next page content

Return ONLY the corrected CURRENT PAGE markdown. No explanations, no preamble."""


def _split_markdown_by_page(markdown: str) -> list:
    """Split Docling markdown into per-page chunks.

    Docling inserts page break markers (horizontal rules or page headers).
    If no clear page breaks are found, split by major heading boundaries
    or by approximate word count chunks.
    """
    # Docling uses "---" horizontal rules as page separators
    # Also try "## Page N" markers from VLM recovery sections
    import re as _re

    # Try splitting on horizontal rules (common Docling page separator)
    parts = _re.split(r'\n---\n', markdown)
    if len(parts) > 1:
        return [p.strip() for p in parts if p.strip()]

    # Fallback: split by top-level headings (# Heading)
    parts = _re.split(r'\n(?=# )', markdown)
    if len(parts) > 1:
        return [p.strip() for p in parts if p.strip()]

    # Last resort: split into ~3000 word chunks respecting paragraph boundaries
    words = markdown.split()
    if len(words) <= 3000:
        return [markdown.strip()]

    chunks = []
    paragraphs = markdown.split('\n\n')
    current_chunk = []
    current_words = 0

    for para in paragraphs:
        para_words = len(para.split())
        if current_words + para_words > 3000 and current_chunk:
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = [para]
            current_words = para_words
        else:
            current_chunk.append(para)
            current_words += para_words

    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))

    return [c.strip() for c in chunks if c.strip()]


def validate_markdown_with_llm(markdown: str) -> str:
    """Use the analyst LLM to validate and fix OCR markdown before DOCX generation.

    Processes markdown using a sliding window: for each page, the LLM receives
    the previous page, current page, and next page as context. Only the current
    page is validated and returned.

    Returns the validated/corrected full markdown.
    """
    pages = _split_markdown_by_page(markdown)

    if not pages:
        logger.warning("No pages to validate — returning original markdown")
        return markdown

    logger.info(f"LLM validation: {len(pages)} page(s) to validate")
    validated_pages = []

    for i, current_page in enumerate(pages):
        prev_page = pages[i - 1] if i > 0 else ""
        next_page = pages[i + 1] if i < len(pages) - 1 else ""

        # Build the prompt with sliding window context
        user_content = ""
        if prev_page:
            user_content += f"=== PREVIOUS PAGE (read-only context) ===\n{prev_page}\n\n"
        else:
            user_content += "=== PREVIOUS PAGE ===\n(This is the first page — no previous page)\n\n"

        user_content += f"=== CURRENT PAGE (validate and fix this) ===\n{current_page}\n\n"

        if next_page:
            user_content += f"=== NEXT PAGE (read-only context) ===\n{next_page}\n"
        else:
            user_content += "=== NEXT PAGE ===\n(This is the last page — no next page)\n"

        # Call analyst LLM
        headers = {
            "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "analyst",
            "messages": [
                {"role": "system", "content": LLM_VALIDATION_PROMPT},
                {"role": "user", "content": user_content}
            ],
            "max_tokens": 8192,
            "temperature": 0
        }

        try:
            resp = requests.post(
                f"{LITELLM_ENDPOINT}/chat/completions",
                json=payload, headers=headers, timeout=300
            )

            if resp.status_code == 200:
                result = resp.json()
                validated = result["choices"][0]["message"].get("content", "").strip()
                # Strip any thinking tags that may leak
                validated = re.sub(r'<think>.*?</think>', '', validated, flags=re.DOTALL).strip()

                if validated:
                    validated_pages.append(validated)
                    logger.info(f"LLM validated page {i + 1}/{len(pages)}: {len(validated)} chars")
                else:
                    # LLM returned empty — keep original
                    validated_pages.append(current_page)
                    logger.warning(f"LLM returned empty for page {i + 1} — keeping original")
            else:
                logger.error(f"LLM validation failed for page {i + 1}: {resp.status_code}")
                validated_pages.append(current_page)

        except Exception as e:
            logger.error(f"LLM validation error for page {i + 1}: {e}")
            validated_pages.append(current_page)

    final_markdown = "\n\n---\n\n".join(validated_pages)
    logger.info(f"LLM validation complete: {len(final_markdown)} chars total")
    return final_markdown




# ===== PENDING CONFIRMATION HELPERS =====

def create_pending_confirmation(conf_id: str, channel_id: str, download_url: str,
                                 original_filename: str, pdf_info: dict,
                                 format_type: str, message_ts: str):
    """Store pending confirmation context in the database."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO pending_confirmations
                   (id, channel_id, download_url, original_filename, pdf_type,
                    total_pages, digital_pages, scanned_pages, format_type, message_ts)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (conf_id, channel_id, download_url, original_filename,
                 pdf_info["type"], pdf_info["total_pages"],
                 pdf_info["digital_pages"], pdf_info["scanned_pages"],
                 format_type, message_ts)
            )
        conn.commit()
    finally:
        conn.close()


def get_pending_confirmation(conf_id: str) -> dict:
    """Retrieve a pending confirmation by ID."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, channel_id, download_url, original_filename, pdf_type,
                          total_pages, digital_pages, scanned_pages, format_type, message_ts
                   FROM pending_confirmations WHERE id = %s""",
                (conf_id,)
            )
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0], "channel_id": row[1], "download_url": row[2],
                    "original_filename": row[3], "pdf_type": row[4],
                    "total_pages": row[5], "digital_pages": row[6],
                    "scanned_pages": row[7], "format_type": row[8],
                    "message_ts": row[9]
                }
            return None
    finally:
        conn.close()


def delete_pending_confirmation(conf_id: str):
    """Remove a pending confirmation after it's been acted on."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pending_confirmations WHERE id = %s", (conf_id,))
        conn.commit()
    finally:
        conn.close()


# ===== IN-PROCESS TOOLS (formerly separate microservices) =====

def split_pdf(pdf_path: str, doc_id: str) -> dict:
    """Split a PDF into per-page PNG images AND extract embedded figures/images.
    Returns: {
        "pages": ["page_0.png", "page_1.png", ...],
        "images": {
            0: ["doc_id_page0_img0.png", ...],
            3: ["doc_id_page3_img0.jpeg"]
        }
    }
    """
    minio_client = get_minio_client()

    # Ensure processed bucket exists
    if not minio_client.bucket_exists("kito-processed-documents"):
        minio_client.make_bucket("kito-processed-documents")

    pages = []
    extracted_images = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        doc = fitz.open(pdf_path)
        for i, page in enumerate(doc):
            # Render at 250 DPI — higher resolution significantly improves Tesseract OCR accuracy
            # (150 DPI loses small font details; 250 DPI is the recommended OCR minimum)
            pix = page.get_pixmap(dpi=250)
            page_filename = f"{doc_id}_page_{i}.png"
            page_path = os.path.join(tmpdir, page_filename)
            pix.save(page_path)

            minio_client.fput_object("kito-processed-documents", page_filename, page_path)
            pages.append(page_filename)

            # 2. Extract embedded images from this page
            page_images = []
            page_rect = page.rect
            page_area = page_rect.width * page_rect.height

            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    img_width = base_image.get("width", 0)
                    img_height = base_image.get("height", 0)

                    # Skip images that are basically the full page scan
                    # (common in scanned PDFs where the whole page is one image)
                    img_area = img_width * img_height
                    if page_area > 0 and img_area > page_area * 0.85:
                        continue

                    # Skip very small images (likely artifacts/icons)
                    if img_width < 50 or img_height < 50:
                        continue

                    img_filename = f"{doc_id}_page{i}_img{img_index}.{image_ext}"
                    img_path = os.path.join(tmpdir, img_filename)

                    with open(img_path, "wb") as f:
                        f.write(image_bytes)

                    minio_client.fput_object("kito-processed-documents", img_filename, img_path)
                    page_images.append(img_filename)
                    logger.info(f"Extracted image from page {i}: {img_filename} ({img_width}x{img_height})")

                except Exception as e:
                    logger.warning(f"Failed to extract image {img_index} from page {i}: {e}")

            if page_images:
                extracted_images[i] = page_images

    return {"pages": pages, "images": extracted_images}


def _call_vlm_on_crop(pil_crop: Image.Image, prompt: str) -> str:
    """Send a cropped image region to the VLM for a focused task (table or figure).
    Returns clean text, stripping any thinking tags.
    """
    buf = io.BytesIO()
    pil_crop.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "builder",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        ]}],
        "max_tokens": 2048
    }

    for attempt in range(3):
        try:
            resp = requests.post(
                f"{BUILDER_VL_ENDPOINT}/chat/completions",
                json=payload, headers=headers, timeout=120
            )
            if resp.status_code == 200:
                msg = resp.json()["choices"][0]["message"]
                content = msg.get("content", "").strip()
                reasoning = msg.get("reasoning", "").strip()
                result = content if content else reasoning
                result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
                return result
            logger.error(f"VLM crop call failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"VLM crop error (attempt {attempt+1}): {e}")
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))

    return ""


def _extract_digital_page(fitz_page) -> str:
    """Extract text from a digital (non-scanned) PDF page using PyMuPDF.
    Returns structured markdown preserving headings, paragraphs, and lists.
    """
    blocks = fitz_page.get_text("dict")["blocks"]
    # Sort blocks top-to-bottom
    blocks = sorted(blocks, key=lambda b: b.get("bbox", [0, 0, 0, 0])[1])

    lines_md = []
    for block in blocks:
        if block.get("type") != 0:  # 0 = text block
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue

            line_text = "".join(s.get("text", "") for s in spans).strip()
            if not line_text:
                continue

            # Determine heading level by font size
            max_size = max(s.get("size", 11) for s in spans)
            is_bold = any(s.get("flags", 0) & 2**4 for s in spans)  # bold flag

            if max_size >= 20 or (max_size >= 16 and is_bold):
                lines_md.append(f"# {line_text}")
            elif max_size >= 15 or (max_size >= 13 and is_bold):
                lines_md.append(f"## {line_text}")
            elif max_size >= 13 or (max_size >= 11 and is_bold):
                lines_md.append(f"### {line_text}")
            else:
                lines_md.append(line_text)

    return "\n\n".join(lines_md)


def _detect_regions(tsv_data: list, img_width: int, img_height: int) -> dict:
    """Analyse Tesseract TSV output to find table and figure candidate regions.

    Returns:
        {
            "tables": [(x0, y0, x1, y1), ...],
            "figures": [(x0, y0, x1, y1), ...]
        }
    """
    # Filter valid word-level rows
    words = [r for r in tsv_data if r["level"] == 5 and r["conf"] > 0 and r["text"].strip()]
    if not words:
        return {"tables": [], "figures": []}

    # --- Table detection ---
    # Group words by (block_num, par_num, line_num) and check for column alignment
    from collections import defaultdict
    line_groups = defaultdict(list)
    for w in words:
        key = (w["block_num"], w["par_num"], w["line_num"])
        line_groups[key].append(w)

    # A block is a table candidate if:
    # - ≥4 lines that share similar x-starting positions (column alignment)
    block_lines = defaultdict(list)
    for key, wlist in line_groups.items():
        block_lines[key[0]].append(wlist)

    table_regions = []
    for block_num, lines in block_lines.items():
        if len(lines) < 3:
            continue
        # Check if x-coordinates of first word per line are clustered (column-like)
        x_starts = [line[0]["left"] for line in lines]
        x_span = max(x_starts) - min(x_starts)
        # If multiple rows have words starting at consistent x positions, it's a table
        # Use a simple heuristic: avg line word count >= 2 and span is small
        avg_words = sum(len(l) for l in lines) / len(lines)
        if avg_words >= 2 and x_span < img_width * 0.6:
            xs = [w["left"] for line in lines for w in line]
            ys = [w["top"] for line in lines for w in line]
            ws = [w["width"] for line in lines for w in line]
            hs = [w["height"] for line in lines for w in line]
            x0 = max(0, min(xs) - 10)
            y0 = max(0, min(ys) - 10)
            x1 = min(img_width, max(x + w for x, w in zip(xs, ws)) + 10)
            y1 = min(img_height, max(y + h for y, h in zip(ys, hs)) + 10)
            region_area = (x1 - x0) * (y1 - y0)
            if region_area > 5000:
                table_regions.append((x0, y0, x1, y1))

    # --- Figure detection ---
    # Tile the page into a grid; tiles with near-zero OCR word density = figure candidate
    figure_regions = []
    tile_h = img_height // 6
    tile_w = img_width

    for row in range(6):
        tile_y0 = row * tile_h
        tile_y1 = min(img_height, (row + 1) * tile_h)
        tile_area = tile_w * (tile_y1 - tile_y0)

        # Words in this tile
        tile_words = [w for w in words if tile_y0 <= w["top"] < tile_y1]
        word_area = sum(w["width"] * w["height"] for w in tile_words)
        density = word_area / tile_area if tile_area > 0 else 1.0

        # Low text density + large tile = likely figure
        if density < 0.03 and tile_area > img_width * img_height * 0.08:
            # Expand vertically to merge adjacent empty tiles
            if figure_regions and figure_regions[-1][3] == tile_y0:
                # Extend previous region
                prev = figure_regions[-1]
                figure_regions[-1] = (prev[0], prev[1], prev[2], tile_y1)
            else:
                figure_regions.append((0, tile_y0, img_width, tile_y1))

    # Remove figure regions that overlap significantly with table regions
    filtered_figures = []
    for freg in figure_regions:
        fx0, fy0, fx1, fy1 = freg
        overlaps = False
        for treg in table_regions:
            tx0, ty0, tx1, ty1 = treg
            inter_y = min(fy1, ty1) - max(fy0, ty0)
            if inter_y > (fy1 - fy0) * 0.3:
                overlaps = True
                break
        if not overlaps:
            filtered_figures.append(freg)

    return {"tables": table_regions, "figures": filtered_figures}


def _extract_text_markdown(tsv_data: list, table_regions: list) -> list:
    """Convert Tesseract TSV word data into structured markdown elements with y-positions.

    Returns list of (y_position, markdown_string) tuples, sorted top-to-bottom.
    Skips words that fall inside detected table regions.
    """
    from collections import defaultdict

    def in_table(x, y):
        for (tx0, ty0, tx1, ty1) in table_regions:
            if tx0 <= x <= tx1 and ty0 <= y <= ty1:
                return True
        return False

    # Group words into lines, skipping table regions
    line_groups = defaultdict(list)
    for row in tsv_data:
        if row["level"] != 5 or row["conf"] <= 0 or not row["text"].strip():
            continue
        if in_table(row["left"], row["top"]):
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        line_groups[key].append(row)

    elements = []
    for key in sorted(line_groups.keys()):
        words = sorted(line_groups[key], key=lambda w: w["left"])
        line_text = " ".join(w["text"] for w in words).strip()
        if not line_text:
            continue
        y_pos = words[0]["top"]
        avg_h = sum(w["height"] for w in words) / len(words)

        # Heuristic heading detection from character height
        if avg_h >= 28:
            md = f"# {line_text}"
        elif avg_h >= 22:
            md = f"## {line_text}"
        elif avg_h >= 18:
            md = f"### {line_text}"
        else:
            md = line_text

        elements.append((y_pos, md))

    return elements


def build_page_ast(image_path: str, fitz_page=None) -> dict:
    """Hybrid OCR pipeline for a single page.

    Strategy:
    1. Auto-detect digital vs scanned (PyMuPDF native text check)
    2. For scanned pages:
       a. Tesseract image_to_string → clean plain text
       b. ONE VLM call to detect+extract tables and figures (structured JSON output)
       c. Save figure crops as PNGs for embedding
    """
    minio_client = get_minio_client()
    page_name = os.path.basename(image_path).replace(".png", "")

    with tempfile.TemporaryDirectory() as tmpdir:
        local_image_path = os.path.join(tmpdir, "page.png")
        minio_client.fget_object("kito-processed-documents", image_path, local_image_path)

        # ── AUTO-DETECT: Digital PDF page? ──────────────────────────────────
        if fitz_page is not None:
            native_text = fitz_page.get_text().strip()
            if len(native_text) > 50:
                logger.info(f"Digital page detected for {image_path} ({len(native_text)} chars)")
                content = _extract_digital_page(fitz_page)
                return {"page": image_path, "content": content, "figures": []}

        # ── SCANNED page ─────────────────────────────────────────────────────
        logger.info(f"Scanned page — running Tesseract OCR for {image_path}")
        pil_img = Image.open(local_image_path)

        # Step 1: Get clean plain text via Tesseract
        # oem 3 = best LSTM engine, psm 3 = auto page segmentation
        tess_config = "--oem 3 --psm 3"
        ocr_text = pytesseract.image_to_string(pil_img, lang="eng", config=tess_config).strip()
        logger.info(f"{image_path}: Tesseract extracted {len(ocr_text)} chars")

        # Step 2: ONE structured VLM call — detect tables and figures
        # Ask VLM to return JSON only. Short, focused prompt = reliable output.
        struct_prompt = (
            "Look at this document page image carefully.\n"
            "Reply with ONLY valid JSON — no explanation, no markdown fences, no commentary.\n\n"
            "Format:\n"
            "{\n"
            '  "has_table": true or false,\n'
            '  "table_markdown": "| col | col |\\n|---|---|\\n| val | val |",\n'
            '  "has_figure": true or false,\n'
            '  "figure_bbox": [x, y, width, height],\n'
            '  "figure_caption": "short description"\n'
            "}\n\n"
            "Rules:\n"
            "- has_table: true only if there is a data table with rows and columns\n"
            "- table_markdown: extract the FULL table as markdown with | separators\n"
            "- has_figure: true only if there is a diagram, chart, graph, or photograph\n"
            "- figure_bbox: pixel coordinates [x, y, width, height] of the figure region\n"
            "- figure_caption: one sentence describing the figure\n"
            "- If no table, set table_markdown to empty string\n"
            "- If no figure, set figure_bbox to null and figure_caption to empty string"
        )

        saved_figures = []
        table_markdown = ""
        figure_block = ""

        try:
            vlm_raw = _call_vlm_on_crop(pil_img, struct_prompt)
            # Strip any markdown code fences the model might add
            vlm_raw = re.sub(r'```(?:json)?\s*', '', vlm_raw).strip().rstrip('`').strip()
            # Strip any thinking leakage before the JSON
            vlm_raw = re.sub(r'^.*?(\{)', r'\1', vlm_raw, flags=re.DOTALL)

            struct = json.loads(vlm_raw)

            # Extract table if present
            if struct.get("has_table") and struct.get("table_markdown", "").strip():
                raw_table = struct["table_markdown"].strip()
                # Keep only lines that look like markdown table rows
                table_lines = [l for l in raw_table.splitlines() if l.strip().startswith("|")]
                if len(table_lines) >= 2:
                    # Ensure separator row exists after header
                    if not re.match(r'\|[-| ]+\|', table_lines[1]):
                        cols = table_lines[0].count("|") - 1
                        sep = "|" + "|".join(["---"] * cols) + "|"
                        table_lines.insert(1, sep)
                    table_markdown = "\n".join(table_lines)
                    logger.info(f"{image_path}: Table extracted ({len(table_lines)} rows)")

            # Extract figure if present
            if struct.get("has_figure"):
                bbox = struct.get("figure_bbox")
                caption = struct.get("figure_caption", "Figure").strip() or "Figure"
                img_w, img_h = pil_img.size

                if bbox and len(bbox) == 4:
                    fx, fy, fw, fh = [int(v) for v in bbox]
                    # Clamp to image bounds
                    fx = max(0, fx); fy = max(0, fy)
                    fw = min(fw, img_w - fx); fh = min(fh, img_h - fy)

                    if fw > 80 and fh > 80:
                        crop = pil_img.crop((fx, fy, fx + fw, fy + fh))
                        fig_filename = f"{page_name}_fig0.png"
                        fig_local = os.path.join(tmpdir, fig_filename)
                        crop.save(fig_local, format="PNG")
                        minio_client.fput_object("kito-processed-documents", fig_filename, fig_local)
                        figure_block = f"__FIGURE__{fig_filename}__CAPTION__{caption}"
                        saved_figures.append(fig_filename)
                        logger.info(f"{image_path}: Figure saved — '{caption}'")

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"{image_path}: VLM structured call failed ({e}) — using OCR text only")

        # Step 3: Assemble final content
        # Order: OCR text → table (if any) → figure (if any)
        parts = [ocr_text]
        if table_markdown:
            parts.append(table_markdown)
        if figure_block:
            parts.append(figure_block)

        content = "\n\n".join(p for p in parts if p.strip())

        logger.info(f"Page {image_path}: {len(content)} chars, {len(saved_figures)} figures")
        return {
            "page": image_path,
            "content": content,
            "figures": saved_figures
        }



def _clean_markdown(markdown: str) -> str:
    """Clean the Docling/VLM markdown before passing to pandoc.

    Removes:
    - <think>...</think> blocks (Qwen3 chain-of-thought leakage)
    - <!-- image --> placeholders that were not matched to a figure
    - Excessive consecutive blank lines (> 2)
    - HTML comment artifacts
    """
    import re as _re
    # Strip <think>...</think> blocks (Qwen3-VL thinking tags)
    markdown = _re.sub(r'<think>.*?</think>', '', markdown, flags=_re.DOTALL)
    # Strip any remaining <!-- ... --> HTML comments (including <!-- image --> leftovers)
    markdown = _re.sub(r'<!--.*?-->', '', markdown, flags=_re.DOTALL)
    # Collapse 3+ consecutive blank lines to 2
    markdown = _re.sub(r'\n{3,}', '\n\n', markdown)
    return markdown.strip()


def call_docling_service(pdf_path: str, doc_id: str, images_dir: str) -> str:
    """Call the Docling GPU sidecar service to convert a PDF to markdown.

    The service runs alongside Ollama in the builder pod on the RTX 5060 Ti.
    It returns markdown with <!-- image --> placeholders + figures as base64.
    We decode and save each figure locally so pandoc can embed them in DOCX.
    """
    logger.info(f"Calling Docling service at {DOCLING_SERVICE_URL}/process-pdf")

    with open(pdf_path, "rb") as f:
        response = requests.post(
            f"{DOCLING_SERVICE_URL}/process-pdf",
            files={"file": (os.path.basename(pdf_path), f, "application/pdf")},
            data={
                "azure_di_endpoint": AZURE_DI_ENDPOINT or "",
                "azure_di_key": AZURE_DI_KEY or "",
            },
            timeout=600
        )

    if response.status_code != 200:
        raise Exception(f"Docling service error {response.status_code}: {response.text[:200]}")

    data = response.json()
    markdown = data["markdown"]
    figures_b64 = data.get("figures", [])
    page_count = data.get("page_count", "?")
    vlm_recovered = data.get("vlm_tables_recovered", 0)

    logger.info(
        f"Docling service: {len(markdown)} chars, {len(figures_b64)} figures, "
        f"{vlm_recovered} VLM tables, {page_count} pages"
    )

    # Save each figure to images_dir with simple names (no absolute paths in markdown)
    fig_names = []
    for idx, fig_b64 in enumerate(figures_b64):
        fig_name = f"fig_{idx:04d}.png"   # simple name — used as relative ref in markdown
        fig_path = os.path.join(images_dir, fig_name)
        with open(fig_path, "wb") as f:
            f.write(base64.b64decode(fig_b64))
        fig_names.append(fig_name)
        logger.info(f"Saved figure {idx} → {fig_path}")

    # Replace <!-- image --> placeholders with relative-path markdown image syntax
    # Pandoc resolves these via --resource-path pointing to images_dir
    fig_counter = [0]

    def replace_img_placeholder(m):
        idx = fig_counter[0]
        fig_counter[0] += 1
        if idx < len(fig_names):
            return f"\n\n![Figure {idx + 1}]({fig_names[idx]})\n\n"
        return ""  # no matching figure — drop placeholder silently

    markdown = re.sub(r'<!-- image -->', replace_img_placeholder, markdown)

    # Clean VLM thinking tags, leftover HTML comments, excess blank lines
    markdown = _clean_markdown(markdown)

    return markdown, data.get("json_data", [])

def reconstitute_pdf_from_json(json_data: list, output_pdf_path: str):
    """Reconstitute an editable PDF from Docling/Azure JSON output using PyMuPDF.
    This creates a new text-searchable PDF which can be accurately converted to DOCX by pdf2docx.
    """
    doc = fitz.open()
    for page_data in json_data:
        # Default A4 page
        page = doc.new_page(width=fitz.paper_size("A4")[0], height=fitz.paper_size("A4")[1])
        y_cursor = 50
        
        provider = page_data.get("provider", "unknown")
        if provider == "azure":
            for item in page_data.get("items", []):
                content = item.get("content", "").strip()
                if not content: continue
                
                # Simple text insertion - in a real scenario we'd use bounding boxes
                # but pdf2docx can handle sequential text blocks decently
                rect = fitz.Rect(50, y_cursor, page.rect.width - 50, y_cursor + 500)
                rc = page.insert_textbox(rect, content, fontsize=11, fontname="helv")
                if rc >= 0:
                    # Successfully inserted, advance cursor
                    y_cursor += 15 + (content.count("\n") * 15)
                else:
                    # Didn't fit, just advance
                    y_cursor += 50
                    
                if y_cursor > page.rect.height - 50:
                    page = doc.new_page(width=fitz.paper_size("A4")[0], height=fitz.paper_size("A4")[1])
                    y_cursor = 50
        
        elif provider == "docling":
            doc_data = page_data.get("doc", {})
            texts = doc_data.get("texts", [])
            for text_item in texts:
                content = text_item.get("text", "").strip()
                if content:
                    rect = fitz.Rect(50, y_cursor, page.rect.width - 50, y_cursor + 500)
                    rc = page.insert_textbox(rect, content, fontsize=11, fontname="helv")
                    y_cursor += 20
                    if y_cursor > page.rect.height - 50:
                        page = doc.new_page(width=fitz.paper_size("A4")[0], height=fitz.paper_size("A4")[1])
                        y_cursor = 50
        
        else:
            # Fallback
            rect = fitz.Rect(50, 50, page.rect.width - 50, page.rect.height - 50)
            page.insert_textbox(rect, str(page_data), fontsize=11, fontname="helv")

    doc.save(output_pdf_path)
    doc.close()
    logger.info(f"Reconstituted PDF saved to {output_pdf_path}")



def generate_document(markdown: str, format_type: str, images_dir: str) -> str:
    """Convert a markdown string to DOCX using pandoc.

    Accepts the markdown output from process_with_docling().
    Images are referenced by local paths inside images_dir and embedded by pandoc.
    Returns the MinIO object name of the generated DOCX.
    """
    minio_client = get_minio_client()

    if not minio_client.bucket_exists("kito-generated-artifacts"):
        minio_client.make_bucket("kito-generated-artifacts")

    file_id = str(uuid.uuid4())
    filename = f"document_{file_id}.docx"

    with tempfile.TemporaryDirectory() as tmpdir:
        md_path = os.path.join(tmpdir, "input.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        local_path = os.path.join(tmpdir, filename)

        # Copy all images into pandoc's working directory so relative paths resolve
        # (pandoc --resource-path only works for relative refs, not absolute paths)
        import shutil
        for fname in os.listdir(images_dir):
            if fname.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                shutil.copy2(os.path.join(images_dir, fname), os.path.join(tmpdir, fname))

        extra_args = [
            f"--reference-doc={REFERENCE_DOC_PATH}",
            "--wrap=none",
            f"--resource-path=.:{tmpdir}",   # . = cwd (tmpdir), also explicit tmpdir
        ]

        try:
            pypandoc.convert_file(
                md_path,
                to="docx",
                format="markdown",              # explicit — never auto-detect
                outputfile=local_path,
                extra_args=extra_args,
                sandbox=False
            )
            logger.info(f"pandoc conversion successful: {local_path}")
        except Exception as e:
            logger.error(f"pandoc conversion failed: {e}")
            raise Exception(f"Document generation failed: {e}")

        minio_client.fput_object("kito-generated-artifacts", filename, local_path)
        logger.info(f"Uploaded artifact: {filename}")

    return filename




# ===== AGENT TOOLS DEFINITION =====

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "convert_pdf_to_word",
            "description": "Convert a PDF document to Word format. Use this tool ONLY when the user's explicit intent is to convert a PDF file to a Word/DOCX document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_format": {
                        "type": "string",
                        "enum": ["docx"],
                        "description": "The desired output format. Must be docx."
                    }
                },
                "required": ["output_format"]
            }
        }
    }
]

SYSTEM_PROMPT = """/no_think
You are Kito, a personal AI assistant on Slack running on a homelab Kubernetes cluster.

Current capabilities:
- Answer questions and have conversations on any topic
- Convert PDF documents to Word/DOCX format

IMPORTANT: You must analyze the user's intent. If the user explicitly asks to convert a PDF to Word, or uploads a PDF and mentions converting it, you MUST use the convert_pdf_to_word tool.
If the user just wants to chat or ask a question, respond directly without using any tools.

Be helpful, concise, and friendly."""


# ===== MAIN AGENT =====

@observe()
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
            timeout=300   # 5 min — allows for cold model reload from disk (~60-90s)
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

        # Fallback for models (like qwen2.5-coder in Ollama) that return tool calls as JSON in the content field
        if not tool_calls and message.get("content"):
            content_str = message.get("content", "").strip()
            try:
                # Strip markdown code blocks if present
                cleaned_content = re.sub(r'^```json\s*|```$', '', content_str, flags=re.IGNORECASE).strip()
                parsed = json.loads(cleaned_content)
                if isinstance(parsed, dict) and "name" in parsed and "arguments" in parsed:
                    tool_calls = [{
                        "function": {
                            "name": parsed["name"],
                            "arguments": json.dumps(parsed["arguments"]) if isinstance(parsed["arguments"], dict) else parsed["arguments"]
                        }
                    }]
                    logger.info(f"Successfully parsed fallback tool call from content: {parsed['name']}")
            except Exception:
                pass

        if tool_calls:
            for tool_call in tool_calls:
                fn_name = tool_call["function"]["name"]
                fn_args_str = tool_call["function"].get("arguments", "{}")

                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                if fn_name == "convert_pdf_to_word" or fn_name == "process_scanned_document":
                    pdf_file = next((f for f in files_metadata if f.get("filetype") == "pdf"), None)
                    if pdf_file:
                        output_format = "docx"
                        post_message_to_slack(
                            channel_id,
                            f"📄 Processing '{pdf_file['name']}' to Word. Starting pipeline..."
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
            # Strip any thinking tags the model may have included
            reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()
            if reply:
                post_message_to_slack(channel_id, reply)

    except Exception as e:
        logger.exception("Agent execution failed")
        post_message_to_slack(channel_id, f"Sorry, something went wrong: {str(e)}")


# ===== DOCUMENT PROCESSING PIPELINE (Smart Detection) =====

def process_document_pipeline(download_url: str, original_filename: str, channel_id: str, format_type: str):
    """Smart document processing pipeline with digital/scanned detection.

    Flow:
    1. Download PDF from Slack → MinIO (archival)
    2. Detect PDF type (digital vs scanned)
    3. Digital → direct pdf2docx conversion → upload to Slack
    4. Scanned → notify user with interactive buttons → wait for confirmation
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

            # 1. Download from Slack and archive to MinIO
            update_job_status(job_id, "downloading")
            logger.info(f"[Job {job_id}] Downloading from Slack...")
            download_slack_file(download_url, local_pdf_path)
            minio_client.fput_object("kito-raw-documents", raw_filename, local_pdf_path)

            # 2. Detect PDF type
            update_job_status(job_id, "detecting")
            logger.info(f"[Job {job_id}] Detecting PDF type...")
            pdf_info = detect_pdf_type(local_pdf_path)

            post_message_to_slack(
                channel_id,
                f"📄 *'{original_filename}'* — {pdf_info['total_pages']} pages detected.\n"
                f"🔎 Type: *{pdf_info['type'].upper()}* "
                f"({pdf_info['digital_pages']} digital, {pdf_info['scanned_pages']} scanned pages)"
            )

            if pdf_info["type"] == "digital":
                # ── DIGITAL PATH: direct pdf2docx conversion ─────────────
                update_job_status(job_id, "converting")
                post_message_to_slack(
                    channel_id,
                    "✨ Digital PDF detected — using direct conversion for best formatting..."
                )

                try:
                    object_name = convert_digital_pdf(local_pdf_path)
                except Exception as e:
                    # Fallback: if pdf2docx fails, try the Docling pipeline
                    logger.warning(f"pdf2docx failed ({e}) — falling back to Docling pipeline")
                    post_message_to_slack(
                        channel_id,
                        "⚠️ Direct conversion had issues — falling back to OCR pipeline..."
                    )
                    images_dir = os.path.join(tmpdir, "images")
                    os.makedirs(images_dir, exist_ok=True)
                    markdown, _ = call_docling_service(local_pdf_path, doc_id, images_dir)
                    markdown = validate_markdown_with_llm(markdown)
                    object_name = generate_document(markdown, format_type, images_dir)

                # Upload to Slack
                update_job_status(job_id, "uploading")
                local_out_path = os.path.join(tmpdir, object_name)
                minio_client.fget_object("kito-generated-artifacts", object_name, local_out_path)

                word_count = "N/A"
                try:
                    fitz_doc = fitz.open(local_pdf_path)
                    word_count = sum(len(page.get_text().split()) for page in fitz_doc)
                    fitz_doc.close()
                except Exception:
                    pass

                comment = (
                    f"✅ Converted '{original_filename}' → {format_type.upper()} (direct conversion)\n"
                    f"📊 {pdf_info['total_pages']} pages · ~{word_count} words"
                )
                upload_file_to_slack(local_out_path, channel_id, object_name, comment)
                update_job_status(job_id, "completed")
                logger.info(f"[Job {job_id}] Digital pipeline completed successfully")

            else:
                # ── SCANNED PATH: Docling -> Editable PDF -> pdf2docx ──────────────
                update_job_status(job_id, "processing")
                post_message_to_slack(
                    channel_id,
                    f"⚠️ *Scanned PDF Detected*\n"
                    f"Reconstituting document layout using AI pipeline..."
                )
                
                images_dir = os.path.join(tmpdir, "images")
                os.makedirs(images_dir, exist_ok=True)
                
                # 1. Run Docling GPU pipeline to get Markdown
                logger.info(f"[Job {job_id}] Calling Docling service for Markdown...")
                markdown, _ = call_docling_service(local_pdf_path, doc_id, images_dir)
                
                # 2. Validate markdown with LLM to fix OCR artifacts
                markdown = validate_markdown_with_llm(markdown)
                
                # 3. Use pandoc to convert Markdown to DOCX directly
                object_name = generate_document(markdown, format_type, images_dir)
                
                # 4. Upload to Slack with approval buttons
                update_job_status(job_id, "uploading")
                local_out_path = os.path.join(tmpdir, object_name)
                minio_client.fget_object("kito-generated-artifacts", object_name, local_out_path)

                # Send file without initial comment because upload API is complex, we just upload it
                upload_file_to_slack(local_out_path, channel_id, object_name, f"Here is the converted Word document for {original_filename}")
                
                conf_id = str(uuid.uuid4())
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Are you satisfied with this conversion for `{original_filename}`?"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✅ Satisfied"},
                                "style": "primary",
                                "action_id": "ocr_satisfied",
                                "value": conf_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "❌ Unsatisfied (Try Azure DI)"},
                                "style": "danger",
                                "action_id": "ocr_unsatisfied",
                                "value": conf_id
                            }
                        ]
                    }
                ]

                fallback_text = f"Are you satisfied with this conversion for '{original_filename}'?"
                message_ts = post_interactive_message(channel_id, fallback_text, blocks)

                # Store the context so we can fall back to Azure DI if needed
                create_pending_confirmation(
                    conf_id, channel_id, download_url, original_filename,
                    pdf_info, format_type, message_ts
                )
                logger.info(f"[Job {job_id}] Scanned pipeline complete, awaiting satisfaction (conf_id={conf_id})")
                update_job_status(job_id, "completed")

    except Exception as e:
        logger.exception(f"[Job {job_id}] Pipeline failed")
        update_job_status(job_id, "failed", error=str(e))
        post_message_to_slack(channel_id, f"❌ Processing failed: {str(e)}")


@observe()
def fallback_to_azure_di(conf_id: str):
    """Fallback pipeline — triggered if user is unsatisfied.
    Uses Azure Document Intelligence direct conversion (if available) or raw processing.
    """
    conf = get_pending_confirmation(conf_id)
    if not conf:
        logger.error(f"Pending confirmation not found: {conf_id}")
        return

    channel_id = conf["channel_id"]
    download_url = conf["download_url"]
    original_filename = conf["original_filename"]
    format_type = conf["format_type"]
    total_pages = conf["total_pages"]

    job_id = str(uuid.uuid4())
    create_job(job_id, channel_id, original_filename, format_type)

    try:
        minio_client = get_minio_client()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_pdf_path = os.path.join(tmpdir, "raw.pdf")

            # Re-download from Slack
            update_job_status(job_id, "downloading")
            download_slack_file(download_url, local_pdf_path)

            post_message_to_slack(
                channel_id,
                f"🔄 Falling back to Azure Document Intelligence for '{original_filename}'..."
            )

            # In a real implementation we would call Azure DI endpoint with docx outputFormat
            # For now, we simulate processing or call the sidecar and use markdown to pandoc
            # as a secondary fallback if Azure direct DOCX isn't configured in sidecar.
            # Here we just use the docling service which uses Azure DI under the hood
            doc_id = str(uuid.uuid4())
            images_dir = os.path.join(tmpdir, "images")
            os.makedirs(images_dir, exist_ok=True)
            
            markdown, _ = call_docling_service(local_pdf_path, doc_id, images_dir)
            object_name = generate_document(markdown, format_type, images_dir)

            local_out_path = os.path.join(tmpdir, object_name)
            minio_client.fget_object("kito-generated-artifacts", object_name, local_out_path)

            comment = f"✅ Fallback completed for '{original_filename}' via Azure Document Intelligence"
            upload_file_to_slack(local_out_path, channel_id, object_name, comment)

        update_job_status(job_id, "completed")
        delete_pending_confirmation(conf_id)
        logger.info(f"[Job {job_id}] Fallback pipeline completed successfully")

    except Exception as e:
        logger.exception(f"[Job {job_id}] Fallback pipeline failed")
        update_job_status(job_id, "failed", error=str(e))
        post_message_to_slack(channel_id, f"❌ Fallback processing failed: {str(e)}")
        delete_pending_confirmation(conf_id)


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
    event_subtype = event.get("subtype")

    # Log event details for debugging
    logger.info(f"Event type: {event.get('type')}, subtype: {event_subtype}, bot_id: {event.get('bot_id')}, files: {len(event.get('files', []))}")

    # Process user messages
    # Allow: no subtype (plain message) and "file_share" (message with file upload)
    # Block: bot messages, and subtypes like message_changed, message_deleted, etc.
    ALLOWED_SUBTYPES = {None, "file_share"}
    if event.get("type") == "message" and not event.get("bot_id") and event_subtype in ALLOWED_SUBTYPES:
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


# ===== SLACK INTERACTIVITY HANDLER (Button Clicks) =====

@app.post("/slack/interactivity")
async def slack_interactivity(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack interactive component payloads (button clicks).

    Slack sends interactivity payloads as form-encoded data with a 'payload' field
    containing a JSON string. We must respond with HTTP 200 within 3 seconds.
    """
    form = await request.form()
    raw_payload = form.get("payload", "")
    if not raw_payload:
        return Response(status_code=400, content="Missing payload")

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return Response(status_code=400, content="Invalid JSON payload")

    logger.info(f"Interactivity payload type: {payload.get('type')}")

    if payload.get("type") == "block_actions":
        actions = payload.get("actions", [])
        channel_id = payload.get("channel", {}).get("id", "")
        message_ts = payload.get("message", {}).get("ts", "")
        user_name = payload.get("user", {}).get("name", "unknown")

        for action in actions:
            action_id = action.get("action_id", "")
            conf_id = action.get("value", "")

            if action_id == "ocr_satisfied":
                logger.info(f"User {user_name} satisfied with OCR for confirmation {conf_id}")

                # Update the original message to remove buttons
                update_slack_message(
                    channel_id, message_ts,
                    f"✅ Process marked as Satisfied by {user_name}.",
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"✅ *Conversion satisfied by {user_name}*."
                        }
                    }]
                )
                delete_pending_confirmation(conf_id)

            elif action_id == "ocr_unsatisfied":
                logger.info(f"User {user_name} unsatisfied with OCR for confirmation {conf_id}")

                # Update the original message to show fallback
                update_slack_message(
                    channel_id, message_ts,
                    f"⚠️ marked Unsatisfied by {user_name}. Trying Azure DI fallback...",
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"⚠️ *Unsatisfied by {user_name}*. Triggering Azure fallback..."
                        }
                    }]
                )

                # Trigger the fallback pipeline in the background
                background_tasks.add_task(fallback_to_azure_di, conf_id)

    return Response(status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)

