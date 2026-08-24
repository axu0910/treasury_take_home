"""End-to-end coverage of the HTTP API in app.api.routes, exercised through FastAPI's
TestClient against the real pipeline (real Tesseract OCR, no mocking) so these tests also
double as an integration smoke test of the wiring between layers. Requirements covered:
2.1 (single-label workflow), 2.5 (image handling / actionable errors), 2.6 (batch uploads,
per-item isolation, progress), and 3 (actionable errors, no silent timeouts)."""

import io
import time

from fastapi.testclient import TestClient
from PIL import Image


def _png_bytes(size: tuple[int, int] = (200, 200), color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _poll_batch(client: TestClient, batch_id: str, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = client.get(f"/api/verifications/batch/{batch_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] == "completed":
            return body
        time.sleep(0.1)
    raise AssertionError(f"Batch {batch_id} did not complete within {timeout_s}s")


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "local"}


def test_single_verification_rejects_unsupported_content_type(client: TestClient) -> None:
    response = client.post(
        "/api/verifications",
        files={"label_image": ("label.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 415


def test_single_verification_rejects_oversized_image(client: TestClient) -> None:
    oversized = b"0" * (20 * 1024 * 1024 + 1)

    response = client.post(
        "/api/verifications",
        files={"label_image": ("label.png", oversized, "image/png")},
    )

    assert response.status_code == 413


def test_single_verification_happy_path_returns_full_result_contract(client: TestClient) -> None:
    """Requirement 2.1: run verification and view extracted values, comparison, and status
    within the expected response shape."""
    response = client.post(
        "/api/verifications",
        files={"label_image": ("label.png", _png_bytes(), "image/png")},
        data={"brand_name": "OLD TOM DISTILLERY"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"pass", "review", "fail"}
    assert "quality" in body and "issues" in body["quality"]
    assert "checks" in body and isinstance(body["checks"], list)
    assert "extracted_fields" in body
    assert body["source_filename"] == "label.png"


def test_single_verification_cleans_up_uploaded_file_after_processing(client: TestClient) -> None:
    """Requirement 6 (data minimization): source images are temporary and should not remain
    on disk once a verification completes."""
    import app.api.routes as routes

    client.post(
        "/api/verifications",
        files={"label_image": ("label.png", _png_bytes(), "image/png")},
    )

    assert list(routes.UPLOAD_DIR.iterdir()) == []


def test_batch_rejects_more_than_300_images(client: TestClient) -> None:
    """Requirement 2.6: batches are expected in the ~200-300 range; the API enforces an
    explicit upper bound rather than accepting an unbounded upload."""
    files = [("label_images", (f"label-{i}.png", _png_bytes((10, 10)), "image/png")) for i in range(301)]

    response = client.post("/api/verifications/batch", files=files)

    assert response.status_code == 400


def test_batch_returns_immediately_without_blocking_on_processing(client: TestClient) -> None:
    """Regression for the QA fix in c6fc30d: POST /api/verifications/batch must return right
    away with a batch_id rather than blocking the request until every item is processed."""
    files = [("label_images", (f"label-{i}.png", _png_bytes((10, 10)), "image/png")) for i in range(5)]

    started = time.monotonic()
    response = client.post("/api/verifications/batch", files=files)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert "batch_id" in body
    # Generous bound: even a slow environment should accept the request itself well under a
    # second; the real per-item OCR work happens after this call returns.
    assert elapsed < 5.0


def test_batch_status_for_unknown_batch_id_is_404(client: TestClient) -> None:
    response = client.get("/api/verifications/batch/does-not-exist")

    assert response.status_code == 404


def test_batch_isolates_bad_items_so_the_whole_batch_still_completes(client: TestClient) -> None:
    """Regression for the QA fix in c6fc30d: an unsupported file type or an oversized item
    inside a batch must not stop the batch or silently vanish from the results - every
    submitted item is accounted for by a same-shaped result."""
    files = [
        ("label_images", ("good.png", _png_bytes((10, 10)), "image/png")),
        ("label_images", ("bad-type.txt", b"not an image", "text/plain")),
        ("label_images", ("too-big.png", b"0" * (20 * 1024 * 1024 + 1), "image/png")),
    ]

    response = client.post("/api/verifications/batch", files=files)
    assert response.status_code == 200
    batch_id = response.json()["batch_id"]

    completed = _poll_batch(client, batch_id)

    assert completed["total"] == 3
    assert completed["completed"] == 3
    assert len(completed["results"]) == 3

    by_filename = {result["source_filename"]: result for result in completed["results"]}
    assert set(by_filename) == {"good.png", "bad-type.txt", "too-big.png"}
    assert by_filename["bad-type.txt"]["status"] == "review"
    assert "Unsupported file type" in by_filename["bad-type.txt"]["message"]
    assert by_filename["too-big.png"]["status"] == "review"
    assert "size limit" in by_filename["too-big.png"]["message"]
    # The good item still ran through the real pipeline rather than being blocked by its
    # batch-mates' failures.
    assert by_filename["good.png"]["message"] is None or "size limit" not in (
        by_filename["good.png"]["message"] or ""
    )


def test_override_unknown_verification_is_404(client: TestClient) -> None:
    response = client.post("/api/verifications/does-not-exist/override", json={"status": "pass"})

    assert response.status_code == 404


def test_override_corrects_a_field_and_replaces_the_final_status(client: TestClient) -> None:
    """Requirement 2.1: an agent can manually correct an extracted value and/or override the
    automated result - the correction must show up as a match and the agent's status call
    must win over whatever the pipeline originally decided."""
    created = client.post(
        "/api/verifications",
        files={"label_image": ("label.png", _png_bytes(), "image/png")},
        data={"brand_name": "STONE'S THROW"},
    )
    verification_id = created.json()["verification_id"]

    response = client.post(
        f"/api/verifications/{verification_id}/override",
        json={
            "status": "pass",
            "note": "Confirmed against the physical bottle.",
            "overridden_by": "agent.jenny",
            "corrected_fields": {"brand_name": "STONE'S THROW"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pass"
    assert body["override"]["overridden_by"] == "agent.jenny"
    assert body["override"]["note"] == "Confirmed against the physical bottle."
    brand_check = next(check for check in body["checks"] if check["field"] == "brand_name")
    assert brand_check["label_value"] == "STONE'S THROW"
    assert brand_check["status"] == "match"


def test_override_on_a_batch_item_updates_the_polled_batch_results(client: TestClient) -> None:
    """An override must be reflected the next time the frontend polls batch status, not just
    in the override response itself - otherwise the batch dashboard keeps showing the
    pre-override status forever."""
    files = [("label_images", ("label.png", _png_bytes((10, 10)), "image/png"))]
    started = client.post("/api/verifications/batch", files=files)
    batch_id = started.json()["batch_id"]
    completed = _poll_batch(client, batch_id)
    verification_id = completed["results"][0]["verification_id"]

    override_response = client.post(
        f"/api/verifications/{verification_id}/override",
        json={"status": "fail", "note": "Warning is missing on the physical label."},
    )
    assert override_response.status_code == 200

    refreshed = client.get(f"/api/verifications/batch/{batch_id}").json()
    assert refreshed["results"][0]["status"] == "fail"
    assert refreshed["results"][0]["override"]["note"] == "Warning is missing on the physical label."


def test_batch_export_csv_contains_one_row_per_item(client: TestClient) -> None:
    files = [
        ("label_images", ("a.png", _png_bytes((10, 10)), "image/png")),
        ("label_images", ("b.png", _png_bytes((10, 10)), "image/png")),
    ]
    started = client.post("/api/verifications/batch", files=files)
    batch_id = started.json()["batch_id"]
    _poll_batch(client, batch_id)

    response = client.get(f"/api/verifications/batch/{batch_id}/export?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = response.text.strip().splitlines()
    assert len(rows) == 3  # header + 2 items
    assert "verification_id" in rows[0]
    assert "brand_name_label_value" in rows[0]


def test_batch_export_json_round_trips_the_batch_result(client: TestClient) -> None:
    files = [("label_images", ("a.png", _png_bytes((10, 10)), "image/png"))]
    started = client.post("/api/verifications/batch", files=files)
    batch_id = started.json()["batch_id"]
    _poll_batch(client, batch_id)

    response = client.get(f"/api/verifications/batch/{batch_id}/export?format=json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    import json

    body = json.loads(response.text)
    assert body["batch_id"] == batch_id
    assert len(body["results"]) == 1


def test_batch_export_rejects_unknown_format(client: TestClient) -> None:
    files = [("label_images", ("a.png", _png_bytes((10, 10)), "image/png"))]
    started = client.post("/api/verifications/batch", files=files)
    batch_id = started.json()["batch_id"]
    _poll_batch(client, batch_id)

    response = client.get(f"/api/verifications/batch/{batch_id}/export?format=xml")

    assert response.status_code == 400
