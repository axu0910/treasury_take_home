"""Shared fixtures for API-level tests. Redirects the upload directory and SQLite database to
a per-test temp location so exercising the HTTP layer never touches the real data/ and
uploads/ directories the app uses outside of tests."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.routes as routes
import app.db.database as database
import app.services.claude_extraction as claude_extraction
import app.services.rapid_ocr as rapid_ocr
from app.main import app


@pytest.fixture(autouse=True)
def _no_real_claude_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force claude_extraction.is_configured() to False for every test by default, regardless
    of whatever ANTHROPIC_API_KEY happens to be set in the shell running pytest. Without this,
    a developer with a real key exported (for manual/local testing, e.g. see README.md
    "Approach") would have every test that exercises app.services.pipeline.verify_label -
    test_api.py's verification/batch endpoints, test_real_label_images.py - silently fire real,
    billed Claude API calls just by running `pytest`. Tests that specifically want to exercise
    the Claude path already monkeypatch claude_extraction.is_configured/extract_with_claude
    directly (see test_pipeline.py) - that per-test monkeypatch simply overrides this default,
    same technique test_claude_extraction.py's own is_configured() test already uses."""
    monkeypatch.setattr(claude_extraction, "ANTHROPIC_API_KEY", None)


@pytest.fixture(autouse=True)
def _no_real_rapidocr_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force rapid_ocr.is_available() to False for every test by default, regardless of
    whether the optional "rapidocr" extra happens to be installed in the environment running
    pytest. Without this, the local-OCR test suite (test_pipeline.py, test_api.py,
    test_real_label_images.py) would behave differently depending on that environment detail -
    tests would silently exercise the RapidOCR+Tesseract merge path on a machine that has the
    extra installed, and the Tesseract-only path everywhere else. Tests that specifically want
    to exercise the merge path monkeypatch rapid_ocr.is_available/read_label directly (see
    test_pipeline.py) - that per-test monkeypatch simply overrides this default, same technique
    _no_real_claude_calls above uses.

    This forces the underlying import to fail (rather than replacing is_available() itself)
    so is_available()'s own real logic still runs and is still directly testable - see
    test_rapid_ocr.py, which further overrides sys.modules per test case to cover both
    outcomes."""
    monkeypatch.setattr(rapid_ocr, "_import_failed", False)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(routes, "UPLOAD_DIR", tmp_path / "uploads")
    routes.UPLOAD_DIR.mkdir()
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "labels.db")
    database.initialize_database()

    return TestClient(app)
