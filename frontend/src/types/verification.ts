export type VerificationStatus = "pass" | "review" | "fail";

export interface VerificationResult {
  verification_id: string;
  source_filename?: string | null;
  status: VerificationStatus;
  processing_time_ms: number;
  quality: {
    image_readable: boolean;
    issues: string[];
    ocr_confidence: number;
  };
  checks: Array<{
    field: string;
    status: "match" | "mismatch" | "missing" | "review";
    application_value: string | null;
    label_value: string | null;
    confidence: number;
    reason?: string | null;
  }>;
  message?: string;
}