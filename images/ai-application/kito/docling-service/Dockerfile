# CUDA 12.8 runtime — matches kubeworker02 driver (RTX 5060 Ti)
FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System deps: tesseract (CLI OCR), OpenCV system libs, python3.11
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-dev \
    python3-pip \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python

# Install CUDA-enabled PyTorch FIRST (cu128 matches kubeworker02 CUDA 12.8)
RUN pip install --no-cache-dir torch torchvision \
    --index-url https://download.pytorch.org/whl/cu128

# Install all runtime dependencies
# - opencv-python-headless: Phase 1 (CLAHE, deskew) + Phase 3 (dewarp)
# - pymupdf: high-DPI page rendering for preprocessing
# - docling: layout analysis, TableFormer, OCR orchestration
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    python-multipart \
    requests \
    numpy \
    opencv-python-headless \
    pymupdf \
    docling \
    Pillow \
    pytesseract \
    azure-ai-documentintelligence \
    azure-core

# Pre-download Docling ML models at build time — baked into image layer
# Avoids cold-start delay on pod startup
RUN python -c "\
from docling.document_converter import DocumentConverter, PdfFormatOption; \
from docling.datamodel.base_models import InputFormat; \
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions; \
opts = PdfPipelineOptions(); \
opts.do_ocr = True; \
opts.do_table_structure = True; \
opts.generate_picture_images = True; \
opts.images_scale = 2.0; \
opts.ocr_options = TesseractCliOcrOptions(); \
DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}); \
print('Docling models pre-downloaded OK') \
"

COPY main.py .

EXPOSE 8100

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
