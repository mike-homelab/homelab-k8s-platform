FROM python:3.12-slim

# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY src/ .

# Non-root user
RUN useradd -u 10001 appuser
USER appuser

EXPOSE 8080

CMD ["python", "main.py"]
