"""
docling-service — GPU-accelerated document intelligence API

Phase 1: Image preprocessing  — CLAHE contrast + deskew before Docling
Phase 2: VLM table recovery   — Qwen3-VL on localhost:11434 for curved/missing tables
Phase 3: Page dewarp          — Cylindrical correction for book spine curvature
"""

import os
import io
import base64
import logging
import asyncio
import tempfile
import requests
import numpy as np
import cv2
import fitz  # PyMuPDF — for high-DPI page rendering in preprocessing
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docling-service")

# ── Docling imports ─────────────────────────────────────────────────────────
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions

# Ollama endpoint — localhost because docling-service is a sidecar in the builder pod
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "builder")  # qwen3-vl:8b with 64K context

# ── Global converter ────────────────────────────────────────────────────────
CONVERTER: DocumentConverter = None
EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _build_converter() -> DocumentConverter:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    pipeline_options.generate_picture_images = True
    pipeline_options.images_scale = 2.0             # 2x resolution for figure extraction
    pipeline_options.ocr_options = TesseractCliOcrOptions()

    logger.info("Loading Docling models (DocLayNet + TableFormer)...")
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    logger.info("Docling models loaded — GPU ready")
    return converter


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CONVERTER
    loop = asyncio.get_event_loop()
    CONVERTER = await loop.run_in_executor(EXECUTOR, _build_converter)
    yield


app = FastAPI(title="Docling Service", version="1.1.0", lifespan=lifespan)


# =============================================================================
# PHASE 3 — Page Dewarp (cylindrical correction for book spine curvature)
# =============================================================================

def dewarp_page(img: np.ndarray) -> np.ndarray:
    """Correct cylindrical page curvature from book spine scanning.

    Detects the curved left margin of text and fits a polynomial to it.
    Applies an inverse horizontal shift to straighten the page.
    """
    if img is None or img.size == 0:
        return img

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img.copy()
    h, w = img.shape[:2]

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Sample leftmost text pixel per row — traces the left margin curve
    left_margin_pts = []
    row_density = binary.sum(axis=1)
    for y in range(0, h, 4):
        if row_density[y] < w * 0.05:
            continue
        nz = np.where(binary[y] > 0)[0]
        if len(nz) > 0:
            left_margin_pts.append((float(y), float(nz[0])))

    if len(left_margin_pts) < 20:
        logger.debug("Not enough text rows for dewarp — skipping")
        return img

    ys = np.array([p[0] for p in left_margin_pts])
    xs = np.array([p[1] for p in left_margin_pts])

    poly = np.polyfit(ys, xs, 2)
    mid_offset = np.polyval(poly, h / 2)

    map_x = np.zeros((h, w), dtype=np.float32)
    map_y = np.zeros((h, w), dtype=np.float32)
    for y in range(h):
        shift = np.polyval(poly, y) - mid_offset
        map_x[y, :] = np.arange(w, dtype=np.float32) + shift
        map_y[y, :] = y

    dewarped = cv2.remap(img, map_x, map_y,
                         interpolation=cv2.INTER_LANCZOS4,
                         borderMode=cv2.BORDER_REPLICATE)
    return dewarped


# =============================================================================
# PHASE 1 — Image Enhancement (CLAHE + deskew)
# =============================================================================

def deskew_image(img: np.ndarray) -> np.ndarray:
    """Correct small rotation angles (< 10 deg) from scanner placement."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img.copy()
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 100:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.5 or abs(angle) > 10:
        return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h),
                          flags=cv2.INTER_LANCZOS4,
                          borderMode=cv2.BORDER_REPLICATE)


def enhance_page(img: np.ndarray) -> np.ndarray:
    """CLAHE contrast enhancement (fixes uneven scan lamp) then deskew."""
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    img = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    img = deskew_image(img)
    return img


def preprocess_pdf(input_pdf: str, output_pdf: str, render_dpi: int = 300) -> str:
    """Phase 1 + 3: render each page at 300 DPI, dewarp, enhance, save new PDF."""
    doc = fitz.open(input_pdf)
    enhanced_images = []

    for page_no, page in enumerate(doc):
        pix = page.get_pixmap(dpi=render_dpi)
        img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        if pix.n == 4:
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)

        # Phase 3 first — straighten curves before contrast enhancement
        img_np = dewarp_page(img_np)
        # Phase 1 — CLAHE + deskew on the now-straightened page
        img_np = enhance_page(img_np)

        enhanced_images.append(Image.fromarray(img_np))
        logger.info(f"Preprocessed page {page_no + 1}/{len(doc)} at {render_dpi} DPI")

    doc.close()

    if not enhanced_images:
        return input_pdf

    enhanced_images[0].save(
        output_pdf, "PDF", resolution=render_dpi,
        save_all=True, append_images=enhanced_images[1:]
    )
    logger.info(f"Saved preprocessed PDF ({len(enhanced_images)} pages): {output_pdf}")
    return output_pdf


# =============================================================================
# PHASE 2 — VLM Table Recovery (Qwen3-VL on localhost)
# =============================================================================

def _encode_pil(pil_img: Image.Image) -> str:
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def ask_vlm_for_table(page_img: Image.Image) -> str:
    """Ask Qwen3-VL to extract a table from a page image.

    Returns clean markdown table string, or "" if no table found.
    Calls localhost:11434 — zero network hop (same pod).
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{_encode_pil(page_img)}"}},
                {"type": "text",
                 "text": (
                     "Look at this scanned document page carefully.\n"
                     "If there is a data table — even with curved, faint, or missing borders — "
                     "extract ALL rows and columns as a complete markdown table using | col | syntax.\n"
                     "If there is NO table, reply with exactly: NO_TABLE\n"
                     "Output ONLY the markdown table or NO_TABLE. No explanation."
                 )}
            ]
        }],
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 8192}
    }

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()

        # Strip <think>...</think> chain-of-thought blocks (Qwen3-VL always emits these)
        import re as _re
        content = _re.sub(r'<think>.*?</think>', '', content, flags=_re.DOTALL).strip()

        if "NO_TABLE" in content.upper():
            return ""

        table_lines = [l for l in content.splitlines() if l.strip().startswith("|")]
        if len(table_lines) < 2:
            return ""

        # Ensure separator row after header
        if not any(c == "-" for c in table_lines[1]):
            cols = table_lines[0].count("|") - 1
            table_lines.insert(1, "|" + "|".join(["---"] * cols) + "|")

        return "\n".join(table_lines)

    except Exception as e:
        logger.warning(f"VLM table recovery request failed: {e}")
        return ""


def recover_missing_tables(doc, original_pdf: str) -> dict:
    """Find pages where Docling found no table OR a malformed table, ask VLM to recover.

    Two triggers for VLM:
    1. Page has ZERO tables — Docling completely missed it (curved/faint borders)
    2. Page has a table with < 2 columns or < 2 data rows — TableFormer gave up

    Returns {page_no (1-indexed): markdown_table_string}.
    """
    pages_need_vlm: set = set()
    pages_with_good_tables: set = set()

    try:
        from docling_core.types.doc import TableItem
        for item, _ in doc.iterate_items():
            if not isinstance(item, TableItem) or not item.prov:
                continue
            page_no = item.prov[0].page_no

            # Inspect table quality
            num_cols, num_rows = 0, 0
            try:
                if item.data:
                    num_cols = getattr(item.data, "num_cols", 0) or 0
                    num_rows = len(item.data.grid) if item.data.grid else 0
            except Exception:
                pass

            if num_cols < 2 or num_rows < 2:
                # Malformed table — VLM should try to replace it
                logger.info(
                    f"Page {page_no}: Docling table has {num_cols} cols x {num_rows} rows "
                    f"(malformed) — flagging for VLM"
                )
                pages_need_vlm.add(page_no)
            else:
                pages_with_good_tables.add(page_no)

    except (ImportError, AttributeError) as e:
        logger.warning(f"Cannot inspect Docling table items: {e} — skipping VLM recovery")
        return {}

    fitz_doc = fitz.open(original_pdf)
    recovered = {}

    for page_no in range(1, len(fitz_doc) + 1):
        if page_no in pages_with_good_tables:
            continue  # Already has a good Docling table

        pix = fitz_doc[page_no - 1].get_pixmap(dpi=200)
        img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        if pix.n == 4:
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)

        reason = "malformed table" if page_no in pages_need_vlm else "no table"
        logger.info(f"Page {page_no}: Docling {reason} — asking VLM...")
        table_md = ask_vlm_for_table(Image.fromarray(img_np))
        if table_md:
            recovered[page_no] = table_md
            logger.info(f"Page {page_no}: VLM recovered table ({len(table_md.splitlines())} rows)")

    fitz_doc.close()
    return recovered


# =============================================================================
# Health check
# =============================================================================

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": CONVERTER is not None}


# =============================================================================
# Core processing endpoint
# =============================================================================

def _process_pdf_sync(pdf_bytes: bytes) -> dict:
    """Full 3-phase pipeline: preprocess -> Docling -> VLM table recovery -> export."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_pdf = os.path.join(tmpdir, "raw.pdf")
        enhanced_pdf = os.path.join(tmpdir, "enhanced.pdf")

        with open(raw_pdf, "wb") as f:
            f.write(pdf_bytes)

        # Phase 1 + 3: preprocess
        logger.info("Phase 1+3: dewarping + enhancing PDF pages...")
        try:
            preprocess_pdf(raw_pdf, enhanced_pdf, render_dpi=300)
            source_for_docling = enhanced_pdf
        except Exception as e:
            logger.warning(f"Preprocessing failed ({e}) — using original PDF")
            source_for_docling = raw_pdf

        # Docling conversion on preprocessed PDF
        logger.info("Docling: converting...")
        result = CONVERTER.convert(source_for_docling)
        doc = result.document

        # Phase 2: VLM table recovery for pages Docling missed
        logger.info("Phase 2: VLM table recovery check...")
        try:
            recovered_tables = recover_missing_tables(doc, raw_pdf)
        except Exception as e:
            logger.warning(f"VLM table recovery error ({e})")
            recovered_tables = {}

        # Export markdown with image placeholders
        try:
            from docling_core.types.doc import ImageRefMode
            markdown = doc.export_to_markdown(image_mode=ImageRefMode.PLACEHOLDER)
        except (ImportError, TypeError):
            markdown = doc.export_to_markdown()

        # Append VLM-recovered tables with page reference
        if recovered_tables:
            section = "\n\n---\n\n## Tables (VLM-recovered)\n\n"
            for page_no in sorted(recovered_tables):
                section += f"### Page {page_no}\n\n{recovered_tables[page_no]}\n\n"
            markdown += section

        # Extract figures at 300 DPI directly from the ORIGINAL PDF
        # (avoids double-rasterization quality loss from preprocessed PDF)
        figures = []
        try:
            from docling_core.types.doc import PictureItem
            orig_fitz = fitz.open(raw_pdf)

            for item, _level in doc.iterate_items():
                if isinstance(item, PictureItem) and item.prov:
                    try:
                        prov = item.prov[0]
                        page_no = prov.page_no - 1  # 0-indexed
                        bbox = prov.bbox             # Docling bbox: (l, t, r, b) in pts

                        fitz_page = orig_fitz[page_no]
                        page_rect = fitz_page.rect   # full page rect in pts

                        # Docling uses bottom-left origin; fitz uses top-left
                        ph = page_rect.height
                        crop_rect = fitz.Rect(
                            bbox.l, ph - bbox.t,
                            bbox.r, ph - bbox.b
                        )
                        # Clamp to page bounds
                        crop_rect &= page_rect

                        # Render the crop at 300 DPI (scale = 300/72)
                        mat = fitz.Matrix(300 / 72, 300 / 72)
                        pix = fitz_page.get_pixmap(matrix=mat, clip=crop_rect)
                        img_bytes = pix.tobytes("png")
                        figures.append(base64.b64encode(img_bytes).decode("utf-8"))
                        logger.info(f"Extracted figure at 300 DPI from page {page_no + 1}")

                    except Exception as e:
                        # Fall back to Docling's built-in extraction
                        logger.warning(f"Direct figure crop failed: {e} — using Docling fallback")
                        try:
                            img = item.get_image(doc)
                            if img:
                                buf = io.BytesIO()
                                img.save(buf, format="PNG")
                                figures.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
                        except Exception:
                            pass

            orig_fitz.close()

        except ImportError:
            logger.warning("PictureItem not available — skipping figure extraction")


        page_count = len(list(doc.pages)) if hasattr(doc, "pages") else "?"
        logger.info(
            f"Complete — {len(markdown)} chars, {len(figures)} figures, "
            f"{len(recovered_tables)} VLM-recovered tables, {page_count} pages"
        )

        return {
            "markdown": markdown,
            "figures": figures,
            "figure_count": len(figures),
            "page_count": page_count,
            "vlm_tables_recovered": len(recovered_tables)
        }


@app.post("/process-pdf")
async def process_pdf(file: UploadFile = File(...)):
    if CONVERTER is None:
        raise HTTPException(status_code=503, detail="Models still loading — retry shortly")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    try:
        pdf_bytes = await file.read()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(EXECUTOR, _process_pdf_sync, pdf_bytes)
        return JSONResponse(content=result)
    except Exception as e:
        logger.exception("Processing failed")
        raise HTTPException(status_code=500, detail=str(e))
