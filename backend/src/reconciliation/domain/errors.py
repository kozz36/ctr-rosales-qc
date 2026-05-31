"""Domain error hierarchy.

All errors carry a structured ``detail`` dict for machine-readable context.
No framework or adapter imports are permitted here.
"""

from __future__ import annotations


class ReconciliationError(Exception):
    """Base for all domain errors."""

    def __init__(self, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail: dict = detail or {}


class IngestionError(ReconciliationError):
    """Raised when the input PDF cannot be opened or is corrupt."""


class ExtractionError(ReconciliationError):
    """Raised when a page cannot be extracted (OCR failure, empty result after deskew)."""


class VisionCapExceededError(ReconciliationError):
    """Raised when the vision LLM call budget is exhausted mid-run.

    ``detail`` keys: ``calls_made``, ``cap``, ``pages_remaining``.
    """
