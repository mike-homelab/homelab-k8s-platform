"""
docling-service — GPU-accelerated document intelligence API

Phase 1: Image preprocessing  — CLAHE contrast + deskew before Docling
Phase 2: Page Content Detection & Routing (Azure DI vs Homelab)
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
import pytesseract
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docling-service")

# ── Docling imports ─────────────────────────────────────────────────────────
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions

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
# PHASE 2 — Page Content Detection & Routing (Azure DI vs Homelab)
# =============================================================================

def render_page_gray(fitz_page, dpi=150) -> np.ndarray:
    pix = fitz_page.get_pixmap(dpi=dpi)
    img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)
    if img_np.ndim == 3:
        img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    return img_np

def detect_table_by_lines(gray_img: np.ndarray) -> bool:
    _, binary = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    grid = cv2.bitwise_and(h_lines, v_lines)
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = gray_img.shape[0] * gray_img.shape[1]
    return any(cv2.contourArea(c) > page_area * 0.005 for c in contours)

def detect_table_by_text_columns(gray_img: np.ndarray) -> bool:
    data = pytesseract.image_to_data(gray_img, output_type=pytesseract.Output.DICT)
    words = []
    for i in range(len(data['level'])):
        if data['text'][i].strip():
            words.append({"x": data['left'][i], "y": data['top'][i], "text": data['text'][i]})
    
    # Group words by row (y-coordinate)
    rows = {}
    for w in words:
        y = w['y']
        # Find existing row within 5px
        found_row = next((r for r in rows if abs(r - y) <= 5), None)
        if found_row is None:
            rows[y] = []
            found_row = y
        rows[found_row].append(w['x'])
        
    # Sort and check for column alignment
    sorted_rows = sorted(rows.keys())
    consecutive_table_rows = 0
    for i in range(len(sorted_rows) - 2):
        row1_xs = sorted(rows[sorted_rows[i]])
        row2_xs = sorted(rows[sorted_rows[i+1]])
        row3_xs = sorted(rows[sorted_rows[i+2]])
        
        if len(row1_xs) >= 3 and len(row2_xs) >= 3 and len(row3_xs) >= 3:
            # Check if they align within 15px
            aligned = True
            for j in range(3):
                if max(abs(row1_xs[j] - row2_xs[j]), abs(row2_xs[j] - row3_xs[j])) > 15:
                    aligned = False
                    break
            if aligned:
                consecutive_table_rows += 1
                if consecutive_table_rows >= 1: # 3 consecutive rows is 1 triplet
                    return True
    return False

def detect_figure_region(gray_img: np.ndarray) -> bool:
    _, binary = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    page_area = gray_img.shape[0] * gray_img.shape[1]
    
    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]
        
        # 1. Area > 2% of page
        if area < page_area * 0.02:
            continue
            
        # 2. Aspect ratio
        aspect_ratio = w / float(h)
        if aspect_ratio < 0.2 or aspect_ratio > 5.0:
            continue
            
        # 3. Fill density
        box_area = w * h
        fill_ratio = area / float(box_area)
        if fill_ratio > 0.7:
            continue
            
        return True
    return False

def classify_page(fitz_page) -> str:
    """Returns 'azure' or 'homelab'."""
    img = render_page_gray(fitz_page, dpi=150)
    if detect_table_by_lines(img):
        return "azure"
    if detect_table_by_text_columns(img):
        return "azure"
    if detect_figure_region(img):
        return "azure"
    return "homelab"


def process_page_with_azure_di(fitz_page, azure_client) -> dict:
    pix = fitz_page.get_pixmap(dpi=300)
    img_data = pix.tobytes("png")
    
    poller = azure_client.begin_analyze_document(
        model_id="prebuilt-layout",
        document=img_data
    )
    result = poller.result()
    
    markdown = ""
    figures = []
    
    # Simple formatting based on paragraphs and tables. We sort by y-coordinate.
    items = []
    if result.paragraphs:
        for p in result.paragraphs:
            y = p.bounding_regions[0].polygon[1] if p.bounding_regions else 0
            items.append((y, "p", p.content))
            
    if result.tables:
        for table in result.tables:
            y = table.bounding_regions[0].polygon[1] if table.bounding_regions else 0
            # Construct MD Table
            grid = {}
            max_row = 0
            max_col = 0
            for cell in table.cells:
                grid[(cell.row_index, cell.column_index)] = cell.content.replace("\n", " ").strip()
                max_row = max(max_row, cell.row_index)
                max_col = max(max_col, cell.column_index)
                
            lines = []
            for r in range(max_row + 1):
                row_cells = [grid.get((r, c), "") for c in range(max_col + 1)]
                lines.append("| " + " | ".join(row_cells) + " |")
                if r == 0:
                    lines.append("| " + " | ".join(["---"] * (max_col + 1)) + " |")
            items.append((y, "table", "\n".join(lines)))
            
    if result.figures:
        for fig in result.figures:
            y = fig.bounding_regions[0].polygon[1] if fig.bounding_regions else 0
            items.append((y, "figure", fig))
            
    items.sort(key=lambda x: x[0])
    
    md_parts = []
    for _, item_type, item_data in items:
        if item_type in ("p", "table"):
            md_parts.append(item_data)
        elif item_type == "figure":
            # Extract figure image using polygon
            poly = item_data.bounding_regions[0].polygon
            x_coords = [p for i, p in enumerate(poly) if i % 2 == 0]
            y_coords = [p for i, p in enumerate(poly) if i % 2 == 1]
            x0, x1 = min(x_coords), max(x_coords)
            y0, y1 = min(y_coords), max(y_coords)
            
            # Crop image
            # convert from azure layout points (inches) to pixels. Azure returns coordinates in inches if image.
            # actually for images, the coordinates are in pixels
            rect = fitz.Rect(x0, y0, x1, y1)
            try:
                fig_pix = fitz_page.get_pixmap(dpi=300, clip=rect)
                figures.append(base64.b64encode(fig_pix.tobytes("png")).decode("utf-8"))
                md_parts.append(f"<!-- image placeholder -->")
            except Exception as e:
                logger.warning(f"Failed to extract figure: {e}")
                
    markdown = "\n\n".join(md_parts)
    return {"markdown": markdown, "figures": figures}


def process_page_with_homelab(fitz_page, tmpdir) -> dict:
    doc = fitz.open()
    doc.insert_pdf(fitz.Document(stream=fitz_page.parent.write(), filetype="pdf"), from_page=fitz_page.number, to_page=fitz_page.number)
    page_pdf_path = os.path.join(tmpdir, f"page_{fitz_page.number}.pdf")
    doc.save(page_pdf_path)
    doc.close()
    
    result = CONVERTER.convert(page_pdf_path)
    docling_doc = result.document
    
    try:
        from docling_core.types.doc import ImageRefMode
        markdown = docling_doc.export_to_markdown(image_mode=ImageRefMode.PLACEHOLDER)
    except (ImportError, TypeError):
        markdown = docling_doc.export_to_markdown()
        
    figures = []
    # fallback to get_image
    try:
        from docling_core.types.doc import PictureItem
        for item, _ in docling_doc.iterate_items():
            if isinstance(item, PictureItem):
                img = item.get_image(docling_doc)
                if img:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    figures.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
    except ImportError:
        pass
        
    return {"markdown": markdown, "figures": figures}


# =============================================================================
# Health check
# =============================================================================

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": CONVERTER is not None}


# =============================================================================
# Core processing endpoint
# =============================================================================

def _process_pdf_sync(pdf_bytes: bytes, azure_di_endpoint: str = "", azure_di_key: str = "") -> dict:
    """Full pipeline: preprocess -> per-page routing -> merge markdown."""
    azure_client = None
    if azure_di_endpoint and azure_di_key:
        try:
            azure_client = DocumentIntelligenceClient(
                endpoint=azure_di_endpoint,
                credential=AzureKeyCredential(azure_di_key)
            )
        except Exception as e:
            logger.error(f"Failed to initialize Azure DI client: {e}")

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_pdf = os.path.join(tmpdir, "raw.pdf")
        enhanced_pdf = os.path.join(tmpdir, "enhanced.pdf")

        with open(raw_pdf, "wb") as f:
            f.write(pdf_bytes)

        # Phase 1 + 3: preprocess
        logger.info("Phase 1+3: dewarping + enhancing PDF pages...")
        try:
            preprocess_pdf(raw_pdf, enhanced_pdf, render_dpi=300)
            source_for_processing = enhanced_pdf
        except Exception as e:
            logger.warning(f"Preprocessing failed ({e}) — using original PDF")
            source_for_processing = raw_pdf

        fitz_doc = fitz.open(source_for_processing)
        page_markdowns = []
        all_figures = []
        azure_pages = 0
        homelab_pages = 0
        
        for page_idx, fitz_page in enumerate(fitz_doc):
            route = classify_page(fitz_page)
            logger.info(f"Page {page_idx+1}: classified={route}")
            
            if route == "azure" and azure_client:
                try:
                    res = process_page_with_azure_di(fitz_page, azure_client)
                    logger.info(f"Page {page_idx+1}: Azure DI extracted {len(res.get('figures', []))} figures")
                    page_markdowns.append(res["markdown"])
                    all_figures.extend(res["figures"])
                    azure_pages += 1
                except Exception as e:
                    logger.error(f"Azure DI failed on page {page_idx+1}: {e} - falling back to homelab")
                    res = process_page_with_homelab(fitz_page, tmpdir)
                    page_markdowns.append(res["markdown"])
                    all_figures.extend(res["figures"])
                    homelab_pages += 1
            else:
                res = process_page_with_homelab(fitz_page, tmpdir)
                page_markdowns.append(res["markdown"])
                all_figures.extend(res["figures"])
                homelab_pages += 1
                
        fitz_doc.close()

        final_markdown = "\n\n---\n\n".join(page_markdowns)

        logger.info(
            f"Complete — {len(final_markdown)} chars, {len(all_figures)} figures, "
            f"{azure_pages} azure pages, {homelab_pages} homelab pages"
        )

        return {
            "markdown": final_markdown,
            "figures": all_figures,
            "figure_count": len(all_figures),
            "page_count": azure_pages + homelab_pages,
            "vlm_tables_recovered": 0
        }


@app.post("/process-pdf")
async def process_pdf(
    file: UploadFile = File(...),
    azure_di_endpoint: str = Form(""),
    azure_di_key: str = Form("")
):
    if CONVERTER is None:
        raise HTTPException(status_code=503, detail="Models still loading — retry shortly")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    try:
        pdf_bytes = await file.read()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(EXECUTOR, _process_pdf_sync, pdf_bytes, azure_di_endpoint, azure_di_key)
        return JSONResponse(content=result)
    except Exception as e:
        logger.exception("Processing failed")
        raise HTTPException(status_code=500, detail=str(e))
