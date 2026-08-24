import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
DATABASE_PATH = DATA_DIR / "labels.db"

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

# Claude-vision label extraction (app.services.claude_extraction) is an opt-in extraction
# backend, only used for a given verification when the caller explicitly requests it
# (use_claude=True - see app.services.pipeline.verify_label) and a key is configured here.
# Local Tesseract OCR (app.services.ocr / app.services.extraction) is the default path
# regardless of whether a key is set, since it's the only one that reliably meets the ~5
# second target in requirements.md section 3 - see verify_label's docstring for the measured
# latency comparison that drove this. Claude also falls back to Tesseract automatically if
# requested without a key configured, or if a call fails.
#
# Sonnet 5, not Opus 5, is the model used when Claude is requested: measured against real
# label photos, Opus 5 took 12-20 seconds per extraction (adaptive extended thinking is on by
# default on Opus 5, unlike Opus 4.7/4.8/Sonnet - real reasoning depth this bounded
# transcription/classification task doesn't need). Sonnet 5 is meaningfully faster and roughly
# 40% the cost per token, though even Sonnet 5 averaged ~10s per real photo in testing -
# nowhere near free, which is exactly why this whole path stays opt-in rather than automatic.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
