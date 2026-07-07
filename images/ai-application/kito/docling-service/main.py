"""
docling-service — GPU-accelerated document intelligence API
Runs as a sidecar in the builder pod (RTX 5060 Ti, kubeworker02).
Receives a PDF, runs Docling pipeline, returns markdown + figures as base64.
"""

import os
import io
import base64
import logging
import asyncio
import tempfile
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docling-service")

# ── Docling imports ─────────────────────────────────────────────────────────
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractOcrOptions

# ── Global converter (loaded once at startup) ──────────────────────────────
CONVERTER: DocumentConverter = None
EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _build_converter() -> DocumentConverter:
    """Initialise Docling pipeline with GPU-accelerated models."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True                      # OCR for scanned pages
    pipeline_options.do_table_structure = True          # TableFormer table extraction
    pipeline_options.generate_picture_images = True     # Extract figures as PIL images
    pipeline_options.ocr_options = TesseractOcrOptions()  # Use system Tesseract binary

    logger.info("Loading Docling models (DocLayNet + TableFormer)...")
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    logger.info("Docling models loaded — GPU ready")
    return converter


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CONVERTER
    loop = asyncio.get_event_loop()
    # Load models in thread so we don't block the event loop
    CONVERTER = await loop.run_in_executor(EXECUTOR, _build_converter)
    yield


app = FastAPI(title="Docling Service", version="1.0.0", lifespan=lifespan)


# ── Health check ──────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": CONVERTER is not None}


# ── Core processing ───────────────────────────────────────────────────────
def _process_pdf_sync(pdf_bytes: bytes) -> dict:
    """Synchronous Docling conversion — run in thread executor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, "input.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        logger.info(f"Docling: converting PDF ({len(pdf_bytes)//1024} KB)...")
        result = CONVERTER.convert(pdf_path)
        doc = result.document

        # ── Export markdown with <!-- image --> placeholders ──────────────
        try:
            from docling_core.types.doc import ImageRefMode
            markdown = doc.export_to_markdown(image_mode=ImageRefMode.PLACEHOLDER)
        except (ImportError, TypeError):
            markdown = doc.export_to_markdown()

        # ── Extract figures as base64 PNG (in document order) ─────────────
        figures = []
        try:
            from docling_core.types.doc import PictureItem
            for item, _level in doc.iterate_items():
                if isinstance(item, PictureItem):
                    try:
                        img = item.get_image(doc)
                        if img:
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            figures.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
                            logger.info(f"Figure {len(figures)} extracted")
                    except Exception as e:
                        logger.warning(f"Could not extract figure: {e}")
        except ImportError:
            logger.warning("docling_core not available — no figure extraction")

        page_count = len(list(doc.pages)) if hasattr(doc, "pages") else "?"
        logger.info(f"Docling: done — {len(markdown)} chars, {len(figures)} figures, {page_count} pages")

        return {
            "markdown": markdown,
            "figures": figures,          # list of base64-encoded PNG strings, in document order
            "figure_count": len(figures),
            "page_count": page_count
        }


@app.post("/process-pdf")
async def process_pdf(file: UploadFile = File(...)):
    """Convert a PDF file to markdown + figures.

    Returns:
        markdown: Full document markdown with <!-- image --> placeholders for figures
        figures:  List of base64-encoded PNG strings in document order
                  (replace each placeholder with the corresponding figure)
        figure_count: int
        page_count: int
    """
    if CONVERTER is None:
        raise HTTPException(status_code=503, detail="Docling models still loading — retry shortly")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    try:
        pdf_bytes = await file.read()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(EXECUTOR, _process_pdf_sync, pdf_bytes)
        return JSONResponse(content=result)
    except Exception as e:
        logger.exception("Docling processing failed")
        raise HTTPException(status_code=500, detail=str(e))
