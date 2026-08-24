# syntax=docker/dockerfile:1
#
# Single-image deployment: builds the React/Vite frontend, then bakes it into
# the FastAPI backend image (which also installs Tesseract) so one container
# serves both the UI and the API from one URL. Local OCR (Tesseract, plus the
# RapidOCR extra installed below) is the default label extraction path (it's
# the only one that reliably meets the ~5s target in requirements.md - see
# app/services/pipeline.py and app/services/rapid_ocr.py for the accuracy split
# between the two). Claude vision (app/services/claude_extraction.py) is an
# explicit per-request opt-in for higher accuracy still when ANTHROPIC_API_KEY
# is set at runtime, and every path falls back gracefully if unavailable or a
# call fails.

# ---- Stage 1: build the React/Vite frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python backend + Tesseract, serving the built frontend ----
FROM python:3.11-slim AS backend

# libgl1 and libglib2.0-0 are runtime shared libraries the RapidOCR extra's opencv-python
# dependency needs to import at all - python:3.11-slim doesn't ship them, and without this,
# `import cv2` fails with "libGL.so.1: cannot open shared object file" the first time RapidOCR
# is used, even though the Python package itself installed fine.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "./backend[rapidocr]"

# Bake the compiled frontend into the location app/main.py serves from.
COPY --from=frontend-build /frontend/dist backend/app/static

RUN mkdir -p /app/data /app/uploads

WORKDIR /app/backend
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
