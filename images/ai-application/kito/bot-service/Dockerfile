FROM python:3.11-slim

WORKDIR /app

# System libraries: tesseract as OCR backend for Docling, libgl for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download Docling ML models at build time so there is no cold-start delay.
# Models: DocLayNet (layout), TableFormer (table structure), EasyOCR fallback weights.
# Total ~600MB added to image — acceptable for homelab; avoids per-pod download.
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

EXPOSE 3000

CMD ["python", "main.py"]
