import { useEffect, useRef, useState, type FormEvent } from "react";
import { AlertCircle, Check, ChevronRight, Images, LoaderCircle, Upload } from "lucide-react";

import { createVerification, getBatchStatus, startBatchVerification, type BatchVerificationResult } from "./api/client";
import type { VerificationResult } from "./types/verification";

const fields = [
  ["brand_name", "Brand Name"],
  ["class_type", "Class / Type"],
  ["alcohol_content", "Alcohol Content / ABV"],
  ["net_contents", "Net Contents"],
  ["producer", "Producer / Bottler"],
  ["country_of_origin", "Country of Origin"]
] as const;

const fieldLabels: Record<string, string> = Object.fromEntries(fields);

type ApplicationFields = Record<(typeof fields)[number][0], string>;
type ReviewMode = "single" | "batch";

const initialFields: ApplicationFields = {
  brand_name: "",
  class_type: "",
  alcohol_content: "",
  net_contents: "",
  producer: "",
  country_of_origin: ""
};

export default function App() {
  const [application, setApplication] = useState(initialFields);
  const [file, setFile] = useState<File | null>(null);
  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [batchResult, setBatchResult] = useState<BatchVerificationResult | null>(null);
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [reviewedPreviewUrl, setReviewedPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [reviewMode, setReviewMode] = useState<ReviewMode>("single");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [batchPreviewUrls, setBatchPreviewUrls] = useState<string[]>([]);
  const requestSequence = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPolling() {
    if (pollTimer.current !== null) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }

  useEffect(() => stopPolling, []);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }

    const nextPreviewUrl = URL.createObjectURL(file);
    setPreviewUrl(nextPreviewUrl);
    return () => URL.revokeObjectURL(nextPreviewUrl);
  }, [file]);

  useEffect(() => {
    const nextPreviewUrls = batchFiles.map((batchFile) => URL.createObjectURL(batchFile));
    setBatchPreviewUrls(nextPreviewUrls);
    return () => nextPreviewUrls.forEach((url) => URL.revokeObjectURL(url));
  }, [batchFiles]);

  useEffect(() => () => {
    if (reviewedPreviewUrl) URL.revokeObjectURL(reviewedPreviewUrl);
  }, [reviewedPreviewUrl]);

  async function submitVerification(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Choose a label image before verifying.");
      return;
    }
    setError(null);
    setResult(null);
    setBatchResult(null);
    setIsSubmitting(true);
    const requestId = ++requestSequence.current;
    const applicationSnapshot = { ...application };
    const submittedFile = file;
    try {
      const nextResult = await createVerification(file, applicationSnapshot);
      if (requestId === requestSequence.current) {
        setReviewedPreviewUrl(URL.createObjectURL(submittedFile));
        setFile(null);
        setResult(nextResult);
      }
    } catch (submissionError) {
      setError(submissionError instanceof Error ? submissionError.message : "Verification failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitBatch() {
    if (!batchFiles.length) {
      setError("Choose two or more label images for a batch review.");
      return;
    }
    stopPolling();
    setError(null);
    setResult(null);
    setBatchResult(null);
    setIsSubmitting(true);
    try {
      const started = await startBatchVerification(batchFiles, { ...application });
      setBatchResult(started);
      if (started.status === "completed") {
        setIsSubmitting(false);
        return;
      }
      pollTimer.current = setInterval(async () => {
        try {
          const progress = await getBatchStatus(started.batch_id);
          setBatchResult(progress);
          if (progress.status === "completed") {
            stopPolling();
            setIsSubmitting(false);
          }
        } catch (pollError) {
          stopPolling();
          setIsSubmitting(false);
          setError(pollError instanceof Error ? pollError.message : "Lost track of batch progress.");
        }
      }, 1000);
    } catch (submissionError) {
      setError(submissionError instanceof Error ? submissionError.message : "Batch verification failed.");
      setIsSubmitting(false);
    }
  }

  function resetSingleReview() {
    setResult(null);
    setReviewedPreviewUrl(null);
    setFile(null);
    setError(null);
  }

  function selectSingleFile(nextFile: File | null) {
    setResult(null);
    setReviewedPreviewUrl(null);
    setFile(nextFile);
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <img className="ttb-logo" src="/TTB_logo_web.png" alt="Alcohol and Tobacco Tax and Trade Bureau" />
        <span className="status-pill">Local processing</span>
      </header>
      <div className="brand-copy app-header-copy">
        <p className="eyebrow">TTB label compliance</p>
        <h1>Label Verification</h1>
      </div>

      <form className="workspace" aria-labelledby="upload-heading" onSubmit={submitVerification}>
        <div className="section-heading">
          <h2 id="upload-heading">New Review: Compare An Application With Its Label</h2>
          <p>Enter the application values, then upload one label image or a batch of label images to verify them.</p>
        </div>
        <div className="mode-switcher" role="tablist" aria-label="Review mode">
          <button className={reviewMode === "single" ? "mode-tab active" : "mode-tab"} type="button" role="tab" aria-selected={reviewMode === "single"} onClick={() => setReviewMode("single")}>Single Review</button>
          <button className={reviewMode === "batch" ? "mode-tab active" : "mode-tab"} type="button" role="tab" aria-selected={reviewMode === "batch"} onClick={() => setReviewMode("batch")}>Batch Review</button>
        </div>
        <div className="form-grid">
          {fields.map(([name, label]) => (
            <label className="field" key={name}>
              <span>{label}</span>
              <input
                value={application[name]}
                onChange={(event) => setApplication((current) => ({ ...current, [name]: event.target.value }))}
                placeholder={`Enter ${label}`}
              />
            </label>
          ))}
        </div>
        {reviewMode === "single" ? (
          <label className="upload-panel">
            <Upload aria-hidden="true" size={28} />
            {previewUrl && <img className="image-preview" src={previewUrl} alt="Selected label preview" />}
            <strong>{file ? file.name : "Choose a label image"}</strong>
            <span>PNG, JPG, or WEBP up to 20 MB</span>
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => selectSingleFile(event.target.files?.[0] ?? null)} />
          </label>
        ) : (
          <section className="batch-panel dedicated-batch-panel" aria-labelledby="batch-heading">
            <div className="batch-panel-copy">
              <Images aria-hidden="true" size={22} />
              <div>
                <p className="eyebrow">Batch workspace</p>
                <h3 id="batch-heading">Review multiple labels together</h3>
                <p>Choose up to 300 images and compare them against the application values above.</p>
              </div>
            </div>
            <label className="batch-picker">
              <span>{batchFiles.length > 0 ? `${batchFiles.length} images selected` : "Choose images"}</span>
              <input type="file" multiple accept="image/png,image/jpeg,image/webp" onChange={(event) => setBatchFiles(Array.from(event.target.files ?? []))} />
            </label>
          </section>
        )}
        {error && <div className="notice error"><AlertCircle size={18} />{error}</div>}
        <div className="review-actions">
          {reviewMode === "single" ? result ? null : <button className="primary-action" type="submit" disabled={isSubmitting}>
            {isSubmitting ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}
            {isSubmitting ? "Verifying locally..." : "Verify label"}
          </button> : <button className="secondary-action batch-run-action" type="button" onClick={submitBatch} disabled={isSubmitting || batchFiles.length < 1}>
            {isSubmitting ? <LoaderCircle className="spin" size={18} /> : <Images size={18} />}
            {isSubmitting && batchResult ? `Checking labels... (${batchResult.completed}/${batchResult.total})` : isSubmitting ? "Starting batch..." : "Run batch checks"}
          </button>}
        </div>
        {result && <VerificationResultView result={result} previewUrl={reviewedPreviewUrl} onReset={resetSingleReview} />}
        {batchResult && <BatchResultView result={batchResult} previewUrls={batchPreviewUrls} />}
      </form>
    </main>
  );
}

function BatchResultView({ result, previewUrls }: { result: BatchVerificationResult; previewUrls: string[] }) {
  const percent = result.total > 0 ? Math.round((result.completed / result.total) * 100) : 0;
  return (
    <section className="result-panel" aria-live="polite">
      <div className="result-header">
        <div>
          <p className="eyebrow">Batch result</p>
          <h2>{result.status === "processing" ? `Checking labels... ${result.completed} of ${result.total}` : `${result.completed} of ${result.total} checked`}</h2>
        </div>
      </div>
      {result.status === "processing" && (
        <div className="batch-progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
          <div className="batch-progress-fill" style={{ width: `${percent}%` }} />
        </div>
      )}
      <div className="batch-results">
        {result.results.map((item, index) => (
          <details className="batch-row" key={item.verification_id}>
            <summary>
              <ChevronRight className="batch-chevron" aria-hidden="true" size={18} />
              {previewUrls[index] && <img className="batch-thumbnail" src={previewUrls[index]} alt="" />}
              <span className="batch-file"><strong>{item.source_filename || `Image ${index + 1}`}</strong><small>{item.verification_id}</small></span>
              <span className={`check-status ${item.status}`}>{item.status}</span>
              <span>{item.checks.filter((check) => check.status === "match").length} matches, {item.checks.filter((check) => check.status !== "match").length} needing review</span>
            </summary>
            <div className="batch-checks">
              {item.checks.map((check) => <div key={check.field}><strong>{fieldLabels[check.field] ?? "Government Warning"}</strong><span className={`check-status ${check.status}`}>{check.status}</span><span>{check.label_value || "Not detected"}</span></div>)}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

function VerificationResultView({ result, previewUrl, onReset }: { result: VerificationResult; previewUrl: string | null; onReset: () => void }) {
  return (
    <section className="result-panel" aria-live="polite">
      <div className="result-header">
        <div>
          <p className="eyebrow">Verification result</p>
          <h2 className={`result-title ${result.status}`}>{result.status}</h2>
        </div>
        <span className="processing-time">{result.processing_time_ms} ms</span>
      </div>
      {result.message && <div className="notice"><AlertCircle size={18} />{result.message}</div>}
      <p className="result-summary">
        OCR confidence: {Math.round(result.quality.ocr_confidence * 100)}%. {result.quality.issues.join(" ")}
      </p>
      <div className="review-layout">
        {previewUrl && <div className="review-image"><img src={previewUrl} alt="Reviewed label" /></div>}
        <div className="checks">
          {result.checks.map((check) => (
            <div className="check-row" key={check.field}>
              <strong>{fieldLabels[check.field] ?? "Government Warning"}</strong>
              <span className={`check-status ${check.status}`}>{check.status}</span>
              <span className="check-values">
                <span>Application: {check.application_value || "Not provided"}</span>
                <span>Label: {check.label_value || "Not detected"}</span>
                {check.reason && <small>{check.reason}</small>}
              </span>
            </div>
          ))}
        </div>
      </div>
      <div className="review-reset"><button className="secondary-action" type="button" onClick={onReset}><Upload aria-hidden="true" size={16} />Review another image</button></div>
    </section>
  );
}
