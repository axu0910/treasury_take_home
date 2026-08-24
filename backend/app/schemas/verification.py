from typing import Literal

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    verification_id: str
    source_filename: str | None = None
    status: Literal["pass", "review", "fail"]
    processing_time_ms: int
    quality: "QualityResult"
    checks: list["FieldCheckResult"] = Field(default_factory=list)
    extracted_fields: "ExtractedFieldsResult"
    raw_text: str = ""
    message: str | None = None
    override: "OverrideInfo | None" = None


class QualityResult(BaseModel):
    image_readable: bool
    issues: list[str] = Field(default_factory=list)
    ocr_confidence: float = 0.0


class FieldCheckResult(BaseModel):
    field: str
    status: Literal["match", "mismatch", "missing", "review"]
    application_value: str | None = None
    label_value: str | None = None
    confidence: float = 0.0
    reason: str | None = None


class ExtractedFieldsResult(BaseModel):
    brand_name: str | None = None
    class_type: str | None = None
    alcohol_content: str | None = None
    net_contents: str | None = None
    producer: str | None = None
    country_of_origin: str | None = None
    government_warning: str | None = None


class OverrideRequest(BaseModel):
    """An agent's manual correction of extracted values and/or the final pass/review/fail
    call - requirements.md 2.1: 'Manually correct extracted values or override an automated
    result.' Automation only ever produces a recommendation; this is how the agent's own
    judgment becomes the final, recorded decision."""

    status: Literal["pass", "review", "fail"]
    note: str | None = None
    overridden_by: str | None = None
    corrected_fields: dict[str, str] = Field(default_factory=dict)


class OverrideInfo(BaseModel):
    status: Literal["pass", "review", "fail"]
    previous_status: str
    note: str | None = None
    overridden_by: str | None = None
    corrected_fields: dict[str, str] = Field(default_factory=dict)
    created_at: str


VerificationResult.model_rebuild()


class BatchVerificationResult(BaseModel):
    batch_id: str
    total: int
    completed: int
    status: Literal["processing", "completed"] = "completed"
    results: list[VerificationResult] = Field(default_factory=list)
