# AI-Powered Alcohol Label Verification Architecture

## System Context

```mermaid
flowchart LR
    Applicant["Importer / Applicant"] -->|"Application fields + label image(s)"| ReviewUI
    Agent["TTB Compliance Agent"] -->|"Upload, review, override, export"| ReviewUI
    ReviewUI["Label Verification Web App"]
    ReviewUI --> Verification["Verification Platform"]
    Verification --> Results["Pass / Review / Fail results"]
    Results --> Agent

    Verification -.->|"Future, out of prototype scope"| COLA["COLA System<br/>Existing .NET platform"]
    Verification --> Audit["Audit and operational logs"]
    Verification -->|"No cloud or paid service dependency"| Local["Local host / local network"]
```

## Technology Decisions

| Layer | Local prototype choice | Responsibility |
| --- | --- | --- |
| Frontend | React + TypeScript + Vite | Upload workflow, progress states, result presentation, evidence review, corrections, overrides, and batch dashboard |
| Backend API | FastAPI | Local HTTP API, request validation, orchestration, result contracts, and error handling |
| OCR and image processing | Tesseract plus OpenCV/Pillow | Local text recognition, preprocessing, quality scoring, and evidence coordinates |
| Compliance logic | Python rules engine | Field normalization, exact warning checks, comparison decisions, confidence thresholds, and review routing |
| Persistence | SQLite | Applications, extracted fields, verdicts, corrections, overrides, batch jobs, and audit events |
| Batch execution | Local worker process and filesystem-backed queue | Asynchronous batch processing, progress, isolation of item failures, and retries |

The React application is a presentation and interaction layer only. It never makes the compliance decision: all OCR, validation, persistence, batch work, and exports are performed locally by the backend. This keeps behavior testable, consistent across clients, and usable with network access disabled.

## Container Architecture

```mermaid
flowchart TB
    subgraph Client[Agent workstation / browser]
        UI[React + TypeScript + Vite<br/>Simple upload and results workflow]
        Preview[Image preview and extracted-field review]
    end

    subgraph Local[Local deployment boundary]
        API[Application API<br/>Single-label and batch endpoints]
        Batch[Batch coordinator<br/>Queue and job status]
        Normalize[Image preprocessing<br/>Crop, rotate, deskew, contrast]
        OCR[Local OCR / AI adapter<br/>Open-source or built-in components]
        Extract[Field extraction<br/>Brand, type, ABV, volume, producer, origin, warning]
        Layout[Layout analyzer<br/>Warning prefix, emphasis, placement]
        Validate[Rules engine<br/>Exact and normalized comparisons]
        Confidence[Confidence and quality scoring]
        Store[(Metadata and results store)]
        Files[(Temporary image storage<br/>Retention-controlled)]
        Logs[(Audit / performance logs)]
    end

    UI -->|Local HTTP / HTTPS| API
    API --> Preview
    API -->|Single image| Normalize
    API -->|Multiple images| Batch
    Batch -->|One job per label| Normalize
    Normalize --> OCR
    OCR --> Extract
    OCR --> Layout
    Extract --> Validate
    Layout --> Validate
    Validate --> Confidence
    Confidence --> Store
    API --> Store
    Normalize --> Files
    OCR --> Files
    API --> Logs
    Batch --> Logs
    Validate --> Logs
    Layout --> Logs

    Store -->|Result payload| API
    API -->|Status, fields, discrepancies, evidence| UI
```

## Verification Flow

```mermaid
sequenceDiagram
    actor Agent
    participant UI as Web UI
    participant API as Application API
    participant P as Image Preprocessor
    participant O as OCR Adapter
    participant E as Field Extractor
    participant R as Rules Engine
    participant S as Results Store

    Agent->>UI: Upload application data and label image
    UI->>API: POST verification request
    API->>P: Normalize image
    P-->>API: Processed image + quality metrics
    API->>O: Read label text and layout
    O-->>API: OCR text, locations, confidence
    API->>E: Map text to required fields
    E-->>API: Extracted fields + confidence
    API->>R: Compare application vs label
    R-->>API: Field verdicts and discrepancies
    API->>S: Persist result and audit event
    S-->>API: Result identifier
    API-->>UI: Verification result
    UI-->>Agent: Show pass, review, or fail with evidence

    alt Low image or OCR confidence
        API-->>UI: Flag manual review and explain quality issue
        Agent->>UI: Correct field or replace image
        UI->>API: Re-run verification
    end
```

## Batch Processing Flow

```mermaid
flowchart LR
    Upload[Upload up to hundreds of applications/images] --> API[Batch API]
    API --> Manifest[Validate files and create batch manifest]
    Manifest --> Queue[(Work queue)]
    Queue --> Worker1[Verification worker]
    Queue --> Worker2[Verification worker]
    Queue --> WorkerN[Verification worker pool]
    Worker1 --> Result[(Per-label results)]
    Worker2 --> Result
    WorkerN --> Result
    Result --> Progress[Progress and error aggregation]
    Progress --> Dashboard[Batch dashboard]
    Dashboard --> Review[Agent reviews exceptions]
    Dashboard --> Export[CSV / JSON export]
```

## Validation Decision Model

```mermaid
flowchart TD
    Start[Application + label image] --> Quality{Image readable?}
    Quality -->|No| Manual[Manual review: request better image]
    Quality -->|Yes| OCRText[Extract text and layout]
    OCRText --> Fields[Identify required fields]
    Fields --> Warning{Government warning exact?}
    Warning -->|No| Fail[Fail: warning missing or incorrect]
    Warning -->|Yes| Match{Application fields match label?}
    Match -->|Yes, high confidence| Pass[Pass: agent confirmation recommended]
    Match -->|No| Review[Review: show mismatched fields]
    Match -->|Uncertain confidence| Review
    Pass --> Agent[Agent confirms or overrides]
    Review --> Agent
    Manual --> Agent
    OCRText --> Error{Processing error?}
    Error -->|Yes| Actionable[Actionable error: retry or replace image]
    Error -->|No| Fields
    Actionable --> Agent

    subgraph Comparisons[Comparison policy]
        Brand[Brand name: case / whitespace normalized]
        Type[Class/type: normalized text comparison]
        ABV[ABV: numeric comparison with accepted formatting]
        Volume[Net contents: normalized units and value]
        Producer[Producer / address: extracted for review]
        Origin[Country of origin: extracted for review]
        ExactWarning[Warning text: exact required text plus formatting checks]
    end

    Brand --> Match
    Type --> Match
    ABV --> Match
    Volume --> Match
    Producer --> Match
    Origin --> Match
    ExactWarning --> Warning
```

## Request and Result Contracts

### Single-label request

```json
{
  "application": {
    "brandName": "OLD TOM DISTILLERY",
    "classType": "Kentucky Straight Bourbon Whiskey",
    "alcoholContent": "45% Alc./Vol. (90 Proof)",
    "netContents": "750 mL",
    "producer": "Example Distillery, Kentucky",
    "countryOfOrigin": "United States"
  },
  "labelImage": "multipart/form-data image",
  "options": {
    "retainArtifacts": false
  }
}
```

### Verification result

```json
{
  "verificationId": "ver_123",
  "status": "pass | review | fail",
  "processingTimeMs": 3200,
  "quality": {
    "imageReadable": true,
    "qualityIssues": [],
    "ocrConfidence": 0.97
  },
  "checks": [
    {
      "field": "brandName",
      "status": "match",
      "applicationValue": "OLD TOM DISTILLERY",
      "labelValue": "OLD TOM DISTILLERY",
      "confidence": 0.99
    },
    {
      "field": "governmentWarning",
      "status": "match",
      "confidence": 0.96,
      "exactText": true,
      "formattingDetected": true,
      "evidence": {
        "textRegion": [120, 840, 980, 940],
        "emphasisDetected": true
      }
    }
  ],
  "requiresHumanReview": false
}
```

## Runtime and Security Boundaries

```mermaid
flowchart TB
  Browser[Browser on local machine] -->|Local HTTPS or loopback| API[Local API service]
  API --> Auth[Local prototype authentication boundary]
  API --> Store[(Local encrypted metadata store)]
  API --> Temp[(Local encrypted temporary files)]
  API --> Logs[(Local audit and performance logs)]
  API --> Offline[Outbound network disabled]

  Temp --> Delete[Delete source images after configured retention period]
  Delete --> Purged[No retained label artifact]

  Policy[Production controls documented for later] --> Auth
  Policy --> Temp
  Policy --> Logs
  Policy -.-> Future[Future cloud / COLA adapters]
```

- **Prototype boundary:** standalone local application; no direct COLA integration, cloud infrastructure, paid APIs, or outbound network dependency.
- **Performance target:** return a single-label result in approximately 5 seconds or less under normal conditions. Batch jobs are asynchronous and expose progress instead of blocking the agent.
- **Human-in-the-loop:** automated results are recommendations. Agents can inspect OCR evidence, correct extracted values, replace poor images, and override the result.
- **AI/OCR boundary:** the adapter uses local/open-source or already-installed components. External providers are not required for the zero-cost prototype, and processing remains functional with outbound traffic disabled.
- **Evidence-first review:** every extracted field includes confidence and source-region evidence where available. Low confidence, missing fields, processing errors, and ambiguous comparisons produce actionable retry or manual-review states instead of automatic approval.
- **Warning handling:** the warning statement is treated as an exact-content check. The rules engine separately records whether `GOVERNMENT WARNING:` is uppercase and whether the required emphasis/layout signal is detected.
- **Data minimization:** retain only application metadata, extracted fields, verdicts, and audit events by default. Source images and processed artifacts use configurable retention and deletion policies.
- **Resilience:** individual batch failures are isolated to a label-level error. The batch continues, and the dashboard identifies failed, completed, and manual-review items.
- **Future integrations:** Azure, paid AI services, and an authenticated COLA adapter may be added only after authorization, cost, data mapping, retention, and federal security requirements are approved. The local verification pipeline remains independent of them.

## Requirement Traceability

| Requirement | Architecture coverage |
| --- | --- |
| Single-label verification | Web UI, API, synchronous preprocessing/OCR/extraction/validation flow |
| Required TTB fields | Field extractor and rules engine cover brand, class/type, ABV, net contents, producer/address, origin, and warning |
| Exact warning validation | OCR text equality plus layout analyzer for uppercase prefix, emphasis, and placement signals |
| Reasonable field normalization | Rules engine applies case/whitespace, numeric, unit, and text normalization while preserving discrepancies |
| Imperfect images | Image preprocessor, quality metrics, retry/replacement path, and manual review threshold |
| Approximately 5-second response | Fast synchronous path, local-first processing, performance logs, and latency monitoring |
| 200-300 item batches | Batch manifest, queue, worker pool, isolated per-label failures, progress dashboard, and export |
| Accessible agent workflow | React UI provides clear upload, verify, review, retry, override, and batch exception actions |
| Standalone prototype | No COLA dependency; no sensitive data required; local processing only |
| Zero-cost local deployment | Local API, local worker process, embedded metadata store, and temporary local artifact directory |
| No outbound dependency | Local/open-source OCR/AI adapter and operation with network access disabled |
| Security and retention | TLS, authentication boundary, encrypted stores, audit events, retention-controlled artifacts |
| Future integrations | Azure, paid AI services, and authenticated COLA adapter reserved behind replaceable boundaries |

## Local Deployment Shape

```mermaid
flowchart LR
  Repo[Source repository] --> Run[Local run command]
  Run --> Web[Local React/Vite dev server or static build]
  Web --> API[Local FastAPI process]
  Run --> Worker[Local batch worker process]
  API --> Worker
  API --> Data[(Local SQLite database)]
  API --> Files[(Local temporary artifact directory)]
  Worker --> Data
  Worker --> Files
  Web --> Metrics[Local structured logs and metrics]
  Worker --> Metrics
```

### Local runtime assumptions

- The API, worker, OCR, rules engine, database, and temporary file storage run on one developer or reviewer machine unless a local-network host is explicitly chosen.
- SQLite or another embedded local database is sufficient for the prototype; no managed database is required.
- A filesystem-backed work queue is sufficient for local batch processing; a hosted queue is not required.
- Uploaded images are stored temporarily and deleted according to the configured retention policy.
- Test data is synthetic or non-sensitive.
- The prototype is not an official COLA submission or regulatory decision system.
