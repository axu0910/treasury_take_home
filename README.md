# Local Alcohol Label Verification

Local-first prototype for comparing alcohol label artwork with application data. The system is designed to run at no cost on one machine or a private local network, with no cloud infrastructure or paid API dependency. This can be scaled into cloud services.

## What It Does

An agent provides application values and a label image. The local backend then:

1. Normalizes the image for OCR.
2. Runs local Tesseract OCR when installed.
3. Extracts the supported label fields.
4. Compares extracted values with the application values.
5. Applies exact government-warning validation and normalized field matching.
6. Returns pass or manual-review results with confidence and discrepancy information.

The React interface owns the workflow and presentation. All OCR, extraction, compliance rules, persistence, and batch processing remain in the Python backend.

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

- Python 3.11 or newer.
- Node.js 20 or newer and npm.
- Tesseract OCR installed locally for real image processing.

On macOS with Homebrew:

```bash
brew install tesseract
```

If Homebrew reports that Tesseract is already installed but the backend cannot find it, restart the backend after running `brew link tesseract` or set the executable explicitly:

```bash
export TESSERACT_CMD="$(brew --prefix tesseract)/bin/tesseract"
```

No cloud account, API key, hosted database, or external service is required.

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

The implementation uses a deterministic pipeline for the compliance-critical path:

```text
React form
	-> FastAPI multipart endpoint
	-> local image preprocessing
	-> Tesseract OCR with confidence and bounding boxes
	-> field extraction
	-> normalized comparisons and exact warning check
	-> SQLite result persistence
	-> agent-facing pass/review result
```

Routine text comparisons normalize case and repeated whitespace so obvious variations such as `STONE'S THROW` and `Stone's Throw` can match. Required warning text is checked as an exact normalized statement; uncertain OCR or formatting results are sent to manual review rather than automatically approved.

Uploaded source images are written to the local temporary upload directory only while a verification runs, then deleted. Verification metadata and results are stored in the local SQLite database.

## Tools Used

| Area | Tool | Purpose |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite | Interactive upload, application fields, progress, and result review |
| Backend | Python, FastAPI, Uvicorn | Local HTTP API and request orchestration |
| OCR | Tesseract, pytesseract | Offline text recognition and confidence data |
| Image processing | Pillow now; OpenCV-compatible service boundary | Orientation, grayscale, contrast, and quality preparation |
| Validation | Python standard library rules | Field normalization, warning checks, and review routing |
| Persistence | SQLite | Local verification results and audit-oriented records |
| Testing | pytest | Backend unit and API tests |

## Assumptions

- Sample applications and label images are synthetic or non-sensitive.
- The prototype runs on one local machine or a trusted local network.
- The agent remains responsible for the final regulatory decision.
- OCR confidence is advisory and cannot replace human judgment.
- Current TTB requirements must be confirmed against official guidance before production use.
- The prototype does not submit decisions to or modify COLA.
- Network access may be disabled during runtime.

## Deployment

The prototype's local-first design (no cloud infrastructure, no paid API, works with outbound access disabled) describes how the *processing pipeline* works, not a ban on ever putting a URL in front of it. Evaluators still need a reachable URL, so the same code that runs locally is also deployed as a single Docker container on a free hosting tier - no code changes, no external OCR/AI service, no paid infrastructure. That container is technically "cloud-hosted," which is a deliberate, acknowledged exception to the letter of the local-only requirement, made only to satisfy the separate "deployed application URL" deliverable; everything the container does still runs the identical local Tesseract binary, with zero outbound calls to any third-party OCR or AI service.

The [Dockerfile](Dockerfile) builds the Vite frontend and copies it into the FastAPI image (which also installs Tesseract), so one container serves both the UI and the API from a single origin - no CORS, no separate frontend host.

### Deploy for free (Render)

1. Push this repository to GitHub (already done if you're reading this from the repo).
2. Create a free account at [render.com](https://render.com) (no credit card required for the free web service tier).
3. In the Render dashboard, choose **New > Blueprint** and point it at this repository - it will pick up [render.yaml](render.yaml) automatically and build [Dockerfile](Dockerfile). (Alternatively, choose **New > Web Service**, select **Docker** as the environment, and leave the Dockerfile path as the repository root.)
4. Select the **Free** instance type and deploy. Render assigns a public `https://<service-name>.onrender.com` URL once the build finishes.

Any other Docker-friendly free host (Fly.io, Hugging Face Spaces with the Docker SDK) works the same way, since the app just needs a normal long-lived container - not a serverless function platform. Serverless hosts such as Vercel or Netlify are not suitable for the backend: their functions can't install the Tesseract system binary, their execution-time limits (seconds) are incompatible with the batch endpoint's 200-300 image workload, and their filesystem isn't persistent across invocations, which the SQLite store and temporary upload handling both rely on.

### Free-tier caveats

- **Cold starts.** Render's free web services spin down after 15 minutes of inactivity and take a moment to wake back up on the next request - the ~5-second response target applies to a warm instance, not the first request after idle.
- **Ephemeral disk.** The free tier's filesystem resets on redeploy/restart, so `data/labels.db` and anything in `uploads/` do not persist across deploys. That's acceptable for a reviewable demo; a production deployment would attach a persistent volume or a managed database instead.

## Security and Data Handling

- No external OCR, AI, cloud storage, or paid API is used at runtime.
- Uploaded images are temporary and are deleted after processing.
- SQLite stores local metadata and verification results.
- Production deployment would require stronger authentication, authorization, encryption, retention policy enforcement, audit controls, and federal security review.

## Current Limitations

- Batch verification runs asynchronously: the API returns immediately with a `batch_id` and a background thread pool (6 concurrent workers) processes items, with the frontend polling `GET /api/verifications/batch/{batch_id}` for live progress. The job registry is in-memory (fine for this prototype's single-process deployment) rather than a durable queue, so in-flight batch progress does not survive a restart; a richer export remains future work.
- Field extraction is intentionally heuristic in this prototype and should be expanded with beverage-type-specific rules and stronger layout analysis.
- Brand names within 0.88-1.0 similarity but not identical after case/whitespace normalization are routed to manual review rather than auto-approved, since that range can't reliably distinguish a harmless formatting difference from a genuine single-character OCR misread. ABV values match within 0.5 percentage points. Both tolerances should be reviewed before production use.
- The prototype passes warning presence when the exact uppercase `GOVERNMENT WARNING:` prefix is detected. OCR does not independently prove full wording, bold typography, or placement, so those remain agent checks.
- Tesseract must be installed locally for OCR. If unavailable, the API returns a manual-review state rather than using a cloud fallback.
- The project is not an official COLA submission system.

See [requirements.md](requirements.md) for the requirement inventory and [arch.md](arch.md) for the detailed architecture and traceability diagrams.

## Current Status

The repository contains a working single-label API/UI vertical slice. Batch processing, advanced layout analysis, and production security controls remain outside the current implementation slice.
