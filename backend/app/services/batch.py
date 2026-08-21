from dataclasses import dataclass


@dataclass(frozen=True)
class BatchProgress:
    batch_id: str
    total: int
    completed: int
    failed: int
    needs_review: int


class LocalBatchWorker:
    """Reserved for filesystem-backed local batch processing."""

    def submit(self, files: list[str]) -> str:
        raise NotImplementedError
