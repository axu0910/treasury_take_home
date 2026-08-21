import type { VerificationResult } from "../types/verification";

export interface BatchVerificationResult {
  batch_id: string;
  total: number;
  completed: number;
  results: VerificationResult[];
}

export async function createVerification(file: File, application: Record<string, string>): Promise<VerificationResult> {
  const formData = new FormData();
  formData.append("label_image", file);
  Object.entries(application).forEach(([key, value]) => formData.append(key, value));

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

export async function createBatchVerification(files: File[], application: Record<string, string>): Promise<BatchVerificationResult> {
  const formData = new FormData();
  files.forEach((file) => formData.append("label_images", file));
  Object.entries(application).forEach(([key, value]) => formData.append(key, value));

  const response = await fetch("/api/verifications/batch", {
    method: "POST",
    body: formData,
    cache: "no-store",
    headers: { "Cache-Control": "no-cache" }
  });
  if (!response.ok) throw new Error("The local batch verification service could not process the images.");
  return response.json() as Promise<BatchVerificationResult>;
}