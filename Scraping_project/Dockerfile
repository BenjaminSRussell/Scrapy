# Multi-stage Dockerfile for UConn Scraping Pipeline
# Phase 10 + #144: lean core images vs ML/OCR Stage 4 images
#
# Targets:
#   base / core / production / stage3 — core deps only (Stage 1–3, scraper); no torch
#   ml / stage4                       — core + requirements-ml.txt + requirements-ocr.txt
#
# Stage 3 is light (datasketch MinHash + extractive summary) and runs on core.
# Stage worker CMDs use #262 entrypoint shims (src.workers.*).
# Expected size: Stage 1–3 images ~2–4 GB smaller without torch/transformers/easyocr.
# AC4: measure with `docker images` after local rebuild (blocked if Docker unavailable).

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for core Python packages (compilers for wheels that need them)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Core Python dependencies only (no torch / transformers / easyocr)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

RUN useradd -m -u 1000 scraper && \
    chown -R scraper:scraper /app

USER scraper

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "-m", "src.main"]


# Lean production / Stage 1–2 image (alias of base)
FROM base AS core

USER scraper
ENV ENVIRONMENT=production
CMD ["python", "-m", "src.main"]


# Backwards-compatible name used by existing compose/k8s
FROM core AS production


# Light Stage 3: same as core (datasketch already in requirements.txt)
FROM core AS stage3
CMD ["python", "-m", "src.workers.stage3_worker"]


# Stage 4 image with ML + OCR extras
FROM base AS ml

USER root

# poppler-utils required by pdf2image for PDF rasterization
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-ml.txt requirements-ocr.txt ./
RUN pip install --no-cache-dir -r requirements-ml.txt -r requirements-ocr.txt

USER scraper
ENV ENVIRONMENT=production \
    SCRAPY_EXTRAS=ml,ocr

CMD ["python", "-m", "src.main"]


FROM ml AS stage4
CMD ["python", "-m", "src.workers.stage4_worker"]
