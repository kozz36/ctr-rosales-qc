"""Port definitions — structural typing interfaces (Protocols).

Adapters implement these contracts; the domain and application layers depend
ONLY on this module, never on concrete adapters.

All Protocols are runtime-checkable (isinstance checks work in tests).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from reconciliation.domain.models import (
    GuiaIdentity,
    MaterialLine,
    ReconciliationRow,
    VisionResult,
)


@runtime_checkable
class DocumentSourcePort(Protocol):
    """Provides page-level access to a PDF document.

    Core interface (required — all implementors must satisfy):
        page_count, render_page, page_text

    Optional extension (rev-3 / D1): concrete adapters MAY additionally expose
        image_coverage_ratio(idx: int) → float
    returning the fraction of the page area covered by raster images (0.0–1.0).
    The pipeline calls this via ``hasattr`` guarding so absence is graceful.
    ``PdfStructureAdapter`` implements it; test fakes may omit it.
    """

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


@runtime_checkable
class IdentityExtractionPort(Protocol):
    """Decodes the identity of a Guía de Remisión from its QR/barcode (rev-2, EXT-011).

    The concrete implementation (``QrBarcodeExtractionAdapter``) lives in the adapter
    layer and MUST NOT be imported by the domain or application layer directly.
    """

    def decode_identity(self, image: bytes) -> GuiaIdentity | None:
        """Return a ``GuiaIdentity`` decoded from *image*, or ``None`` on failure.

        ``None`` signals that QR decoding failed or confidence gating rejected the
        result; the caller MUST fall back to OCR-derived identity (EXT-014).
        """
        ...


class OfficialGre(Protocol):
    """Structured data returned by SUNAT GRE fetch (rev-2, EXT-016 — seam only).

    Fields are intentionally minimal; the seam is off by default and exists only
    as an extension point for future opt-in SUNAT integration.
    """

    guia_id: str
    fecha_emision: object  # date
    ruc_emisor: str
    ruc_receptor: str


@runtime_checkable
class SunatGreFetchPort(Protocol):
    """SEAM for future opt-in SUNAT GRE integration (rev-2, EXT-016).

    OFF BY DEFAULT — enabling this port requires ``sunat_fetch.enabled: true``
    in config.  When disabled the caller MUST NOT invoke this port and the
    adapter MUST return ``None`` without any network call.

    Electronic date and quantity from SUNAT GRE are cross-check data only:
    - MUST NOT override the handwritten reception fecha for grouping.
    - MUST NOT override OCR quantities for reconciliation.
    - Enabling this port breaks the local-first / air-gap invariant.
    """

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        """Fetch official GRE data for *hashqr_url* from SUNAT, or ``None``.

        Returns ``None`` when the port is disabled or the fetch fails.
        """
        ...
