# AI-Powered Alcohol Label Verification App Requirements

## 1. Product Goal

Build a standalone prototype that helps TTB compliance agents verify alcohol beverage label applications faster by comparing application data with label artwork.

## 2. Functional Requirements

### 2.1 Single-label verification

The application must allow an agent to:

- Upload a label image.
- Enter or provide the corresponding application fields.
- Run an automated verification.
- View the extracted label values.
- Compare label values with application values.
- See clear pass, review, or fail results.
- See the specific fields that do not match.
- Inspect enough evidence to make a final judgment.
- Manually correct extracted values or override an automated result.

### 2.2 Required label fields

The verification workflow should support these common TTB label fields:

- Brand name.
- Class or type designation.
- Alcohol content / ABV.
- Net contents.
- Bottler or producer name and address.
- Country of origin for imports.
- Government health warning statement.

### 2.3 Government warning validation

The application must treat the government warning as an exact compliance check:

- The required warning text must be present.
- The wording must match the required statement exactly.
- The `GOVERNMENT WARNING:` prefix must be uppercase.
- The application should detect or flag whether the required emphasis and presentation are present, including bold formatting where technically detectable.
- Missing, altered, abbreviated, or incorrectly formatted warnings must be flagged for rejection or manual review.

### 2.4 Field comparison

The comparison logic should account for reasonable formatting variation without hiding meaningful discrepancies:

- Brand names should support case and whitespace normalization, such as `STONE'S THROW` and `Stone's Throw`.
- Numeric fields such as ABV should be compared by value rather than superficial formatting.
- Units and common formatting should be normalized for net contents.
- Class/type, producer, address, and country-of-origin differences should be surfaced clearly.
- Low-confidence extraction or ambiguous matches must be routed to manual review.

### 2.5 Image handling

The prototype should support label images that are not perfect when practical:

- Detect unreadable or unusable images.
- Provide image-quality feedback.
- Attempt basic preprocessing such as rotation, cropping, deskewing, contrast adjustment, or glare reduction where feasible.
- Allow the agent to replace a poor image and retry verification.
- Do not automatically approve a result when image or OCR confidence is insufficient.

### 2.6 Batch uploads

The application must support batch processing for peak periods:

- Upload many label applications at once, with an expected use case of approximately 200 to 300 items.
- Validate the batch manifest and individual files.
- Process labels independently so one failure does not stop the batch.
- Show batch progress and per-item status.
- Identify completed, failed, passed, and manual-review items.
- Allow agents to review exceptions.
- Provide a useful export such as CSV or JSON where practical.

## 3. Performance Requirements

- A normal single-label verification should return results in approximately 5 seconds or less.
- The workflow must feel faster than manual review for routine matching.
- Batch processing may be asynchronous, but the interface must provide visible progress rather than appearing blocked.
- Processing failures should return actionable errors instead of timing out silently.

## 4. User Experience Requirements

The interface must be usable by agents with widely varying technical comfort levels, including users who are not highly computer-literate:

- Clean and obvious workflow.
- Minimal navigation and minimal hunting for controls.
- Clear labels and plain language.
- Prominent upload, verify, review, and retry actions.
- Results that are easy to scan.
- Discrepancies and required next steps that are immediately understandable.
- Support for reviewing one item or working through a batch queue.

## 5. Architecture and Integration Requirements

- The prototype must be standalone.
- The frontend should use React with TypeScript and Vite for the interactive agent workflow.
- The frontend should communicate with the local backend through a documented HTTP API.
- Compliance rules, OCR, image processing, persistence, batch orchestration, and exports must remain in the backend rather than being implemented in the browser.
- Direct COLA integration is out of scope for the prototype.
- The architecture should leave room for a future authenticated COLA adapter.
- The prototype must run entirely on the local machine or local network.
- The prototype must use no cloud infrastructure and incur no infrastructure or API cost.
- OCR, image processing, and AI-assisted extraction must run locally or use free, already-installed/open-source components.
- The solution must work with outbound network access disabled.
- Any external OCR or AI service is out of scope for the prototype and may only be considered as a future adapter.
- The approach should avoid the prior scanning-vendor problem of 30 to 40 second processing times.

## 6. Security, Privacy, and Operations

Production-oriented design should account for:

- PII and sensitive document handling.
- Document retention policies.
- Controlled retention and deletion of uploaded images and processed artifacts.
- Secure transport and storage.
- Authentication and authorization boundaries.
- Auditability of automated decisions, agent corrections, and overrides.
- Restricted outbound network access and service allowlisting.
- Monitoring for latency, failures, and batch queue depth.

For this prototype, no sensitive data should be required and the implementation should avoid unnecessary complexity.

## 7. Deliverables

The project must include:

- Source code repository containing all source code.
- README with setup and run instructions.
- Brief documentation describing the approach, tools used, and assumptions.
- Deployed application URL that evaluators can access and test.

## 8. Evaluation Criteria

The implementation will be assessed on:

- Correctness and completeness of core requirements.
- Code quality and organization.
- Appropriateness of technical choices for the scope.
- User experience and error handling.
- Attention to the stated requirements.
- Creative problem-solving.
- Preference for a working core application with clean code over ambitious but incomplete features.
- Clear documentation of tradeoffs and limitations.

## 9. Scope Classification

### Required for the core prototype

- Single-label upload and verification.
- Required field extraction and comparison.
- Exact warning-text validation.
- Clear discrepancy results.
- Human review and override.
- Approximately 5-second normal-case response target.
- Simple, accessible agent workflow.
- Documentation and setup instructions.

### Required or strongly requested enhancement

- Batch uploads for approximately 200 to 300 items.
- Batch progress and isolated per-item failures.
- Image preprocessing for imperfect photographs.
- Exportable batch results.

### Future or production consideration

- Direct COLA integration.
- Azure or other cloud deployment.
- Federal production authorization and compliance controls.
- Full PII and document-retention implementation.
- Production-grade authentication, authorization, and audit infrastructure.
- Advanced detection of warning typography, layout, and visual compliance.

## 10. Requirements Tensions and Resolution

The brief does contain practical tensions:

| Tension | Recommended resolution |
| --- | --- |
| Approximately 5-second single-label response versus difficult images | Use a fast synchronous path, basic preprocessing, confidence thresholds, and manual review for hard cases. |
| Large batch uploads versus responsive UI | Process batches asynchronously with a queue, worker pool, progress reporting, and per-item errors. |
| Exact warning wording and bold formatting versus OCR limitations | Use OCR for text equality and layout/computer-vision signals for formatting; route uncertain cases to an agent. |
| Human judgment versus automated pass/fail results | Treat automation as a recommendation and preserve agent confirmation, correction, and override actions. |
| Prototype simplicity versus production security expectations | Keep the prototype standalone and low-data, while documenting production security boundaries and deferred controls. |

## 11. Assumptions

- The prototype may use synthetic or non-sensitive sample applications and label images.
- All required processing is available with network access disabled.
- The application does not need to modify COLA or submit official regulatory decisions.
- The agent remains accountable for the final compliance decision.
- TTB-specific validation rules should be confirmed against current official guidance before production use.
- OCR and image-quality confidence are advisory signals and are not a substitute for agent judgment.
