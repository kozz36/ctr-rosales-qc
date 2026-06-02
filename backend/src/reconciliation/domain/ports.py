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
    OfficialGre,
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

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
        """Return a ``GuiaIdentity`` decoded from *image*, or ``None`` on failure.

        ``None`` signals that QR decoding failed or confidence gating rejected the
        result; the caller MUST fall back to OCR-derived identity (EXT-014).

        Args:
            image:    PNG or JPEG bytes of a rendered page.
            page_idx: Optional 0-based page index for audit logging.
        """
        ...


@runtime_checkable
class SunatGreFetchPort(Protocol):
    """OPT-IN SUNAT descargaqr fetch adapter (rev-3, EXT-023 / D3).

    Promoted from a future seam (rev-2 EXT-016) to a first-class OPT-IN
    deterministic data source.  Remains OFF BY DEFAULT behind ``sunat.enabled``
    config flag.

    When enabled: performs a plain HTTP GET on the ``hashqr_url`` (the hashqr
    is the token — NO OAuth, NO Clave SOL) and parses the returned GRE PDF
    to yield authoritative line items (quantities, units, descriptions) and
    the GRE delivery date (``fecha_entrega``) used as the lower bound for
    bounded year inference (D5).

    When disabled: the pipeline MUST NOT invoke this port.

    Invariants (never violated even when SUNAT is enabled):
    - MUST NOT override the handwritten reception ``fecha`` for grouping (EXT-017 / REC-C01).
    - Enabling this port is the ONLY network egress; air-gap default preserved.
    - A fetch failure (timeout, non-200, non-PDF, parse error) MUST return ``None``
      and MUST NOT abort the run (graceful fallback to OCR).
    """

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        """Fetch official GRE data for *hashqr_url* from SUNAT, or ``None``.

        Returns the parsed ``OfficialGre`` on success, or ``None`` on any
        failure (network, parse, non-PDF) so the caller can fall back to OCR.

        Args:
            hashqr_url: The full SUNAT descargaqr URL decoded from the URL-variant
                        QR on the guía page (e.g.
                        ``https://e-factura.sunat.gob.pe/v1/contribuyente/
                        gre/comprobantes/descargaqr?hashqr=<BASE64>``).
        """
        ...
