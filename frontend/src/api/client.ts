import type { VerificationResult } from "../types/verification";

export interface BatchVerificationResult {
  batch_id: string;
  total: number;
  completed: number;
  status: "processing" | "completed";
  results: VerificationResult[];
}

export async function createVerification(
  file: File,
  application: Record<string, string>,
  useClaude = false
): Promise<VerificationResult> {
  const formData = new FormData();
  formData.append("label_image", file);
  Object.entries(application).forEach(([key, value]) => formData.append(key, value));
  formData.append("use_claude", String(useClaude));

  const response = await fetch("/api/verifications", {
    method: "POST",
    body: formData,
    cache: "no-store",
    headers: {
      "Cache-Control": "no-cache"
    }
  });

  if (!response.ok) {
    throw new Error("The local verification service could not process the image.");
  }

  return response.json() as Promise<VerificationResult>;
}

export async function startBatchVerification(
  files: File[],
  application: Record<string, string>,
  useClaude = false
): Promise<BatchVerificationResult> {
  const formData = new FormData();
  files.forEach((file) => formData.append("label_images", file));
  Object.entries(application).forEach(([key, value]) => formData.append(key, value));
  formData.append("use_claude", String(useClaude));

  const response = await fetch("/api/verifications/batch", {
    method: "POST",
    body: formData,
    cache: "no-store",
    headers: { "Cache-Control": "no-cache" }
  });
  if (!response.ok) throw new Error("The local batch verification service could not start the batch.");
  return response.json() as Promise<BatchVerificationResult>;
}

export async function getBatchStatus(batchId: string): Promise<BatchVerificationResult> {
  const response = await fetch(`/api/verifications/batch/${batchId}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Could not fetch batch progress.");
  return response.json() as Promise<BatchVerificationResult>;
}

export interface OverridePayload {
  status: "pass" | "review" | "fail";
  note?: string;
  overridden_by?: string;
  corrected_fields?: Record<string, string>;
}

export async function overrideVerification(verificationId: string, payload: OverridePayload): Promise<VerificationResult> {
  const response = await fetch(`/api/verifications/${verificationId}/override`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error("Could not save the correction.");
  return response.json() as Promise<VerificationResult>;
}

export function batchExportUrl(batchId: string, format: "csv" | "json"): string {
  return `/api/verifications/batch/${batchId}/export?format=${format}`;
}