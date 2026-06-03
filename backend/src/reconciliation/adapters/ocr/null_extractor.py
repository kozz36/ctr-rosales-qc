"""NullOcrExtractor — no-op ExtractionPort for OCR-disabled mode.

When ``config.ocr.enabled=False``, ``build_pipeline`` (container.py) injects
this adapter in place of ``PrintedTableAdapter`` / ``CompositeExtractionAdapter``
for the OCR path.

Contract:
  - ``extract_printed_table`` always returns ``[]`` — no PaddleOCR import,
    no paddle initialisation.
  - ``extract_declared`` always returns ``[]`` — OCR-disabled mode never reads
    digital text through the OCR path (declared pages use the digital text
    adapter, which is not disabled).
  - The adapter is pure Python with no heavy dependencies; it is safe to import
    in any environment, including those where PaddleOCR is broken.

This is a Null Object pattern implementation of ``ExtractionPort``.
"""

from __future__ import annotations

from reconciliation.domain.models import MaterialLine


class NullOcrExtractor:
    """No-op OCR extractor — returns empty lists; never touches PaddleOCR."""

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        """Return an empty list without performing any OCR."""
        return []

    def extract_declared(self, text: str) -> list[MaterialLine]:
        """Return an empty list (OCR-disabled mode; declared pages use digital text adapter)."""
        return []
