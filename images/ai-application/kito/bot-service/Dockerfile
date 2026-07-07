FROM python:3.11-slim

WORKDIR /app

# System libraries needed by PyMuPDF and python-docx
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 3000

CMD ["python", "main.py"]
