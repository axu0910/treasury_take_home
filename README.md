# Alcohol Label Verification

A prototype for comparing alcohol label artwork with application data, for TTB compliance agent review.

## What It Does

An agent provides application values and a label image. The backend then:

1. Normalizes the image (EXIF orientation, size).
2. Reads the label with a local Tesseract OCR pipeline by default - the only path that reliably lands near the ~5 second target in requirements.md section 3. An agent can opt into Claude vision instead (a "Use Claude AI extraction" checkbox in the UI, or `use_claude=true` on the API) for higher accuracy on hard photos, at the cost of several extra seconds and a paid API call per image - see "Approach" below for the measured tradeoff. Claude extraction silently falls back to local OCR if it's requested without `ANTHROPIC_API_KEY` configured, or if a Claude call fails.
3. Extracts the supported label fields.
4. Compares extracted values with the application values.
5. Applies exact government-warning validation and normalized field matching.
6. Returns pass, review, or fail results with confidence and discrepancy information.
7. Lets the agent correct an extracted value or override the automated result, recording who made the change, why, and what the automated result had been.

The React interface owns the workflow and presentation. All extraction, compliance rules, persistence, overrides, and batch processing remain in the Python backend. Single-label review and up to 200-300 image batches are both supported, with per-item batch progress and a CSV/JSON export of batch results.

## Project Structure

```text
frontend/                 React + TypeScript + Vite interface
	src/App.tsx             Initial review/upload screen
	src/api/                Backend API client
	src/types/              Shared frontend response types
backend/                  FastAPI and Python services
	app/api/                HTTP routes
	app/core/               Local paths and configuration
	app/db/                 SQLite access
	app/schemas/            API contracts
	app/services/           Image, OCR, extraction, validation, and batch modules
	tests/                  Backend tests
data/                     Local SQLite data directory
uploads/                  Retention-controlled temporary images
```

## Local Development

### Prerequisites

- Python 3.11 or newer (3.11/3.12 recommended - see the RapidOCR note below).
- Node.js 20 or newer and npm.
- Tesseract OCR installed locally - required, since it's part of the default extraction path (see below).
- An Anthropic API key, optional, only needed if you want the "Use Claude AI extraction" opt-in path available.

On macOS with Homebrew:

```bash
brew install tesseract
```

If Homebrew reports that Tesseract is already installed but the backend cannot find it, restart the backend after running `brew link tesseract` or set the executable explicitly:

```bash
export TESSERACT_CMD="$(brew --prefix tesseract)/bin/tesseract"
```

**Optional: RapidOCR**, the second half of the default local extraction path (see "Approach" below). It's an optional extra (`pip install -e ".[test,rapidocr]"` instead of the plain command below) because its `rapidocr-onnxruntime` dependency currently caps out at **Python <3.13** - on a newer interpreter, skip the extra and the app runs on Tesseract alone (`app/services/rapid_ocr.py`'s `is_available()` check degrades gracefully). The Docker deployment image (`python:3.11-slim`) always installs it.

To make the opt-in "Use Claude AI extraction" path available, set an API key before starting the backend:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Get a key at [console.anthropic.com](https://console.anthropic.com). Optionally set `CLAUDE_MODEL` to override the default (`claude-sonnet-5` - chosen over Opus 5 for latency: Opus 5 measured 12-20s per extraction against real label photos, since it runs adaptive extended thinking by default). `claude-haiku-4-5` is available for an even faster/cheaper tradeoff at lower accuracy. Without a key, the app still runs fully - Claude is never the default path, so nothing degrades; the checkbox in the UI just isn't effective. No hosted database or other external service is required either way.

### Start the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
uvicorn app.main:app --reload --port 8000
```

The backend is available at `http://127.0.0.1:8000`. The health check is:

```bash
curl http://127.0.0.1:8000/api/health
```

### Start the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite, normally `http://localhost:5173`. The Vite development server proxies `/api` requests to the local FastAPI server.

### Run tests and checks

```bash
cd backend
pytest
```

For a syntax-only check that does not require installed dependencies:

```bash
cd ..
python3 -m compileall -q backend
```

## Approach

```text
React form
	-> FastAPI multipart endpoint
	-> image preprocessing (EXIF orientation, size)
	-> local OCR: RapidOCR (brand/class/ABV/net contents/producer/origin) + Tesseract (government warning) - default
	   or Claude vision extraction (opt-in: "Use Claude AI extraction" checkbox / use_claude=true)
	-> normalized comparisons and exact warning check
	-> SQLite result persistence
	-> agent-facing pass/review/fail result
```

Extraction backends all produce the same `ExtractedFields` shape, so everything downstream (comparison, status decision, persistence, the API contract) is identical regardless of which one(s) ran - see `app/services/pipeline.py`:

- **Local OCR (default)** - two engines, split by what each is actually good at, not run as alternatives:
  - **RapidOCR** (`app/services/rapid_ocr.py`, ONNXRuntime-based, optional extra - see "Prerequisites") drives brand name, class/type, ABV, net contents, producer, and country of origin. Measured against real label photos it reads normal print meaningfully more accurately than Tesseract's classical engine - it correctly recovered ABV/net-contents on several real photos that Tesseract-alone returned as `null`.
  - **Tesseract** (`app/services/ocr.py` + `app/services/extraction.py`) still supplies the government warning field specifically, even when RapidOCR is available. RapidOCR has a real failure mode on small dense print - it frequently drops the spaces between words entirely rather than misreading a character - and that print size is exactly where the warning statement is printed; a glued-together read can never pass the exact-text compliance check even when every character is right. Tesseract's word-level bounding boxes don't share that failure mode. (RapidOCR's warning read is used only as a last resort, if Tesseract found none at all.)
  - Both engines run once each, never a second pass - see "Current Limitations" for the multi-pass fallback this replaced and why it was removed. Measured on the real sample photos in `uploads/`, this combination stayed under ~3.5 seconds even on full-resolution phone photos, comfortably inside the ~5 second target in requirements.md section 3, and neither engine makes a network call.
  - If the RapidOCR extra isn't installed, or its read fails, every field falls back to Tesseract alone automatically - the app never depends on RapidOCR being present.
- **Claude vision** (`app/services/claude_extraction.py`, opt-in only): sends the image to Claude with a structured-output schema and reads all seven fields back directly in one call. A vision model can judge *which line is which field* - brand vs. an address vs. marketing copy vs. the warning - from the image itself, a semantic judgment call neither local engine can make (RapidOCR/Tesseract only improve raw character recognition, not line classification); it's meaningfully more accurate on hard photos and doesn't share either local engine's misclassification or space-gluing failure modes. The tradeoff: measured against the real sample photos in `uploads/`, Claude vision (Sonnet 5, low reasoning effort) averaged ~10 seconds per image and ranged up to ~21 seconds - several times slower than local OCR, plus a real per-call API cost - so it's an explicit per-request opt-in (a checkbox in the UI, or `use_claude=true` on the API) rather than the automatic default. Requesting it without `ANTHROPIC_API_KEY` configured, or a Claude call failing, falls back to local OCR with an explanatory note in the result's `message`.

The government warning check takes precedence over the rest of the comparison regardless of which backend produced the fields: a missing, altered, or abbreviated warning yields an overall `fail`, matching the exact-compliance-check treatment the warning statement requires. Other field mismatches or ambiguous matches route to `review`; a label where everything matches, including the warning, is a `pass`.

Routine text comparisons normalize case and repeated whitespace so obvious variations such as `STONE'S THROW` and `Stone's Throw` can match. Required warning text is checked as an exact normalized statement; uncertain or low-confidence results are sent to manual review rather than automatically approved. A result is only auto-approved as `pass` when every field matches *and* the extraction backend's own confidence clears a minimum threshold (Claude's self-reported read confidence, or the blended average per-word OCR confidence across whichever local engine(s) ran) - a run of coincidental matches against a low-confidence read is routed to `review` instead, per requirements.md 2.5 ("Do not automatically approve a result when image or OCR confidence is insufficient").

Uploaded source images are written to the local temporary upload directory only while a verification runs, then deleted. Verification metadata and results are stored in the local SQLite database. When Claude extraction is used, the label image is sent to the Anthropic API as part of that request; it is not stored by this application beyond the temporary upload directory either way.

Automated results are always a recommendation. An agent can edit any extracted label value inline and record a final pass/review/fail decision with an optional name and note; the correction is applied to the stored result immediately (including inside an in-progress batch, so the dashboard reflects it on the next poll) and a separate append-only `overrides` table keeps the full history of what changed, when, and by whom, per requirements.md section 6 ("Auditability of automated decisions, agent corrections, and overrides").

## Tools Used

| Area | Tool | Purpose |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite | Interactive upload, application fields, progress, and result review |
| Backend | Python, FastAPI, Uvicorn | HTTP API and request orchestration |
| Extraction (default) | RapidOCR (ONNXRuntime), Tesseract/pytesseract | Local, no-network OCR - RapidOCR drives most fields, Tesseract specifically supplies the government warning (see "Approach") |
| Extraction (opt-in) | Claude API (vision), `anthropic` SDK | Direct field extraction from the label image via structured output, requested per verification for higher accuracy at higher latency/cost |
| Image processing | Pillow | EXIF orientation, downscaling, and quality preparation |
| Validation | Python standard library rules | Field normalization, warning checks, and review routing |
| Persistence | SQLite | Verification results and audit-oriented records |
| Testing | pytest | Backend unit and API tests (Claude calls are mocked in tests - see `tests/test_claude_extraction.py`) |

## Assumptions

- Sample applications and label images are synthetic or non-sensitive.
- The agent remains responsible for the final regulatory decision.
- Extraction confidence (from either backend) is advisory and cannot replace human judgment.
- Current TTB requirements must be confirmed against official guidance before production use.
- The prototype does not submit decisions to or modify COLA.

## Deployment

The same code runs locally or deployed; the default local-OCR path behaves identically either way. The only difference is whether `ANTHROPIC_API_KEY` is set in the environment, which determines whether the opt-in "Use Claude AI extraction" checkbox actually does anything (checked with no key configured falls back to Tesseract with a note) - see `app/core/config.py`.

The [Dockerfile](Dockerfile) builds the Vite frontend and copies it into the FastAPI image (which also installs Tesseract, required for the default path), so one container serves both the UI and the API from a single origin - no CORS, no separate frontend host.

### Deploy for free (Render)

1. Push this repository to GitHub (already done if you're reading this from the repo).
2. Create a free account at [render.com](https://render.com) (no credit card required for the free web service tier).
3. In the Render dashboard, choose **New > Blueprint** and point it at this repository - it will pick up [render.yaml](render.yaml) automatically and build [Dockerfile](Dockerfile). (Alternatively, choose **New > Web Service**, select **Docker** as the environment, and leave the Dockerfile path as the repository root.)
4. Select the **Free** instance type and deploy. Render assigns a public `https://<service-name>.onrender.com` URL once the build finishes.
5. Optionally, in the service's **Environment** tab, add `ANTHROPIC_API_KEY` (see [render.yaml](render.yaml) - it's declared with `sync: false` so Render won't ask for it in the Blueprint flow, only in the dashboard) to make the opt-in Claude extraction checkbox usable. The default local-OCR path works either way.

Any other Docker-friendly free host (Fly.io, Hugging Face Spaces with the Docker SDK) works the same way, since the app just needs a normal long-lived container - not a serverless function platform. Serverless hosts such as Vercel or Netlify are not suitable for the backend: their functions can't install the Tesseract system binary, their execution-time limits (seconds) are incompatible with the batch endpoint's 200-300 image workload, and their filesystem isn't persistent across invocations, which the SQLite store and temporary upload handling both rely on.

### Free-tier caveats

- **Cold starts.** Render's free web services spin down after 15 minutes of inactivity and take a moment to wake back up on the next request - the ~5-second response target applies to a warm instance, not the first request after idle.
- **Ephemeral disk.** The free tier's filesystem resets on redeploy/restart, so `data/labels.db` and anything in `uploads/` do not persist across deploys. That's acceptable for a reviewable demo; a production deployment would attach a persistent volume or a managed database instead.
- **Image and memory footprint.** The RapidOCR extra adds real weight to the Docker image (onnxruntime, opencv, and its bundled recognition models - roughly 150-200MB of additional installed dependencies) and holds an ONNXRuntime session in memory once first used. This fit comfortably within local testing, but a free-tier instance's RAM ceiling is worth watching if the deployed service seems to struggle - `app/services/rapid_ocr.py`'s `is_available()` check means the app still runs on Tesseract alone if this ever becomes a problem in a given deployment.

## Security and Data Handling

- By default, extraction runs entirely locally via RapidOCR and Tesseract and no image data leaves the machine. Only when an agent explicitly checks "Use Claude AI extraction" (and `ANTHROPIC_API_KEY` is set) is that verification's label image sent to the Anthropic API as part of the extraction request; see [Anthropic's API data usage policy](https://privacy.anthropic.com/) for how that data is (and isn't) retained on their side. No other external service is used at runtime.
- Uploaded images are temporary and are deleted from local storage after processing either way.
- SQLite stores local metadata and verification results.
- Production deployment would require stronger authentication, authorization, encryption, retention policy enforcement, audit controls, and federal security review - and, if Claude extraction is used in production, a data processing agreement covering label-image transmission.

## Current Limitations

- Batch verification runs asynchronously: the API returns immediately with a `batch_id` and a background thread pool (6 concurrent workers) processes items, with the frontend polling `GET /api/verifications/batch/{batch_id}` for live progress and an `/export?format=csv|json` endpoint for exceptions review outside the browser. The job registry is in-memory (fine for this prototype's single-process deployment) rather than a durable queue, so in-flight batch progress does not survive a restart. A batch upload also applies one shared set of application field values to every image in the batch rather than a per-item manifest - fine for a single applicant's multi-label submission, but a batch mixing several applicants' data would need a manifest format (e.g. one row per image) that this prototype doesn't yet parse.
- The default local path's *field-classification* logic (`app/services/extraction.py`, regex/keyword-based line classification) is unchanged by which OCR engine feeds it, and is noticeably weaker than Claude's semantic read on a back-label-only photo dominated by marketing copy - it can pick a prominent marketing sentence as `brand_name` instead of the actual (smaller/absent-from-frame) brand line. This is a "which line is which field" judgment call, not a character-recognition problem, so RapidOCR's better raw text quality doesn't fix it by itself; only Claude's direct semantic read does. This is an accepted, deliberate tradeoff: it's what keeps the default path fast and free, with Claude available as an explicit opt-in for an agent who hits a genuinely hard image and wants the more accurate (but slower, paid) read.
- A second full OCR pass on the raw, unprocessed original was tried as a "best of two" safety net for hard images, but measured against real phone photos it was consistently the single most expensive step (nearly doubling total time, e.g. 2.4s -> 6.7s on a 4032x3024 photo) and it disproportionately triggered on exactly the hardest images - the ones that could least afford it. It was removed; each local engine now runs exactly once per verification. The accuracy risk this fallback used to mitigate is still backstopped by the existing low-confidence-routes-to-review behavior below, and independently by adding RapidOCR alongside Tesseract instead.
- Brand names within 0.88-1.0 similarity but not identical after case/whitespace normalization are routed to manual review rather than auto-approved, since that range can't reliably distinguish a harmless formatting difference from a genuine misread. ABV values match within 0.5 percentage points; net contents are normalized across mL/L/cl/fl oz and match within 1 mL after conversion. All tolerances should be reviewed before production use.
- The prototype requires the exact uppercase `GOVERNMENT WARNING:` prefix and the full required statement text (27 CFR 16.21) to match after whitespace normalization; anything short of that - missing, altered, abbreviated, or unreadable - fails the check. Neither extraction backend independently proves bold typography or placement, so those remain agent checks.
- Claude extraction (opt-in) adds real request latency and a per-call API cost - measured at ~10s average, up to ~21s on a real photo, against ~5s or less for the local default. A Claude outage, rate limit, or auth failure falls back to Tesseract automatically rather than failing the verification, at the cost of that fallback's weaker accuracy on hard images (see the bullet above).
- Preprocessing corrects EXIF camera-orientation metadata (e.g. a portrait phone photo stored with a rotation tag) and downscales very large source images before extraction to keep processing time and request size bounded, but there is no deskew or glare-reduction and no detection of a label that is physically rotated in-frame (e.g. a bottle photographed lying on its side) - those still route to manual review via low field coverage / low confidence rather than being auto-corrected.
- The ~5-second single-label target is what the default local OCR path (RapidOCR + Tesseract) is designed around, and held across the real sample photos tested in `uploads/` (max observed ~3.5s, including full-resolution phone photos); a very large, low-quality, or heavily backgrounded phone photo can still take a couple seconds longer. The Claude opt-in path does not meet this target - see above - which is exactly why it's opt-in rather than automatic.
- The project is not an official COLA submission system.

See [requirements.md](requirements.md) for the requirement inventory and [arch.md](arch.md) for the detailed architecture and traceability diagrams.

## Current Status

The repository contains a working single-label and batch API/UI vertical slice: upload, verification, extracted-field review, agent correction/override with an audit trail, and batch upload with progress, per-item isolation, and CSV/JSON export. Advanced layout analysis (bold/placement detection for the warning statement) and production security controls (auth, encryption at rest, retention enforcement) remain outside the current implementation slice.
