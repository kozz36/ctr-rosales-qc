"""Port definitions — structural typing interfaces (Protocols).

Adapters implement these contracts; the domain and application layers depend
ONLY on this module, never on concrete adapters.

All Protocols are runtime-checkable (isinstance checks work in tests).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from reconciliation.domain.models import MaterialLine, ReconciliationRow, VisionResult


@runtime_checkable
class DocumentSourcePort(Protocol):
    """Provides page-level access to a PDF document."""

    def page_count(self) -> int:
        """Return the total number of pages."""
        ...

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        """Render page *idx* as PNG bytes at *dpi* resolution."""
        ...

    def page_text(self, idx: int) -> str | None:
        """Return embedded digital text for page *idx*, or None if the page is scanned."""
        ...


@runtime_checkable
class ExtractionPort(Protocol):
    """Extracts material lines from declared text or guía images."""

    def extract_declared(self, text: str) -> list[MaterialLine]:
        """Parse declared material list from embedded digital text (no OCR)."""
        ...

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        """Extract material+quantity rows from a guía page image via OCR."""
        ...


@runtime_checkable
class VisionLLMPort(Protocol):
    """Provider-agnostic vision LLM for extracting handwritten dates."""

    supports_batch: bool

    def read_handwritten_date(
        self,
        image: bytes,
        hint: str | None = None,
    ) -> VisionResult:
        """Extract a handwritten date from the stamp crop of a guía page."""
        ...

    def read_handwritten_date_batch(
        self,
        images: list[bytes],
    ) -> list[VisionResult]:
        """Batch variant — only valid when ``supports_batch`` is True."""
        ...


@runtime_checkable
class ReportPort(Protocol):
    """Exports reconciliation results to a file."""

    def export(
        self,
        rows: list[ReconciliationRow],
        audit_trail: list[dict],  # type: ignore[type-arg]
        dst: Path,
        fmt: Literal["xlsx", "csv"],
    ) -> Path:
        """Write the reconciliation report to *dst* and return the output path."""
        ...
