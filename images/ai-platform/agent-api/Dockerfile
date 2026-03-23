FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy complete src/ folder tree into /app/src/
COPY src/ ./src/

RUN useradd -u 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
# Run as module to enable relative imports
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
