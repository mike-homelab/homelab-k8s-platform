# CUDA 12.8 runtime — matches kubeworker02 driver (RTX 5060 Ti)
FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System deps: tesseract (OCR backend for Docling), python3.11, pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-dev \
    python3-pip \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python

# Install CUDA-enabled PyTorch FIRST so docling uses the GPU build
# cu128 matches CUDA 12.8 on kubeworker02
RUN pip install --no-cache-dir torch torchvision \
    --index-url https://download.pytorch.org/whl/cu128

# Install Docling and API server (torch already installed above, won't be replaced)
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    python-multipart \
    docling \
    Pillow

# Pre-download Docling ML models at build time — baked into image layer
# Models: DocLayNet (layout ~300MB) + TableFormer (table structure ~150MB)
# Uses Tesseract as OCR backend — no EasyOCR download needed
RUN python -c "\
from docling.document_converter import DocumentConverter, PdfFormatOption; \
from docling.datamodel.base_models import InputFormat; \
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractOcrOptions; \
opts = PdfPipelineOptions(); \
opts.do_ocr = True; \
opts.do_table_structure = True; \
opts.generate_picture_images = True; \
opts.ocr_options = TesseractOcrOptions(); \
DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}); \
print('Docling models pre-downloaded OK') \
"

COPY main.py .

EXPOSE 8100

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
