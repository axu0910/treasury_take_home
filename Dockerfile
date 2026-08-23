# syntax=docker/dockerfile:1
#
# Single-image deployment: builds the React/Vite frontend, then bakes it into
# the FastAPI backend image (which also installs Tesseract) so one container
# serves both the UI and the API from one URL. Tesseract runs locally inside
# this container - no external OCR/AI service is contacted at runtime.

# ---- Stage 1: build the React/Vite frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python backend + Tesseract, serving the built frontend ----
FROM python:3.11-slim AS backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ./backend

# Bake the compiled frontend into the location app/main.py serves from.
COPY --from=frontend-build /frontend/dist backend/app/static

RUN mkdir -p /app/data /app/uploads

WORKDIR /app/backend
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
