"""FIX 2 (rev-5) — SUNAT line replacement must preserve requires_review on
ocr_fallback blocks.

Bug (JD round-4 confirmed WARNING): ``_apply_sunat_result`` rebuilds the block's
lines from the OfficialGre line items with the default ``requires_review=False``.
When SUNAT enriches an **ocr_fallback** block (the compact identity QR failed but
the URL ``hashqr=`` QR decoded → SUNAT fetch by hashqr_url succeeded), the C1
uncertain-identity review flag is ERASED even though the material is enriched.
Default app mode is SUNAT-enabled + OCR-on, so this is a production path.

Fix: SUNAT lines for an ocr_fallback block carry ``requires_review=True``;
QR-identified blocks stay ``False``.

STRICT TDD: these tests are RED against the pre-fix ``_apply_sunat_result``
(flag erased) and GREEN after.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

from reconciliation.application.pipeline import _GuiaBlock
from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.domain.models import (
    GreLineItem,
    MaterialLine,
    OfficialGre,
    VisionResult,
)


# ---------------------------------------------------------------------------
# Minimal fakes (no external deps) — reuse the pipeline directly.
# ---------------------------------------------------------------------------


class _FakeDoc:
    def page_count(self) -> int:
        return 0

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return b"\x89PNG"

    def page_text(self, idx: int) -> str | None:
        return None


class _FakeExtractor:
    def extract_declared(self, text: str) -> list[MaterialLine]:
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return []


class _FakeVision:
    supports_batch: bool = False

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:
        return VisionResult(date=date(2026, 5, 1), confidence=0.99, raw="01/05/2026")


_VALID_UNITS = frozenset({"KG", "TN", "RD", "Rollo"})
_DOMAIN_UNIT = Literal["KG", "TN", "RD", "Rollo"]


def _make_pipeline() -> ReconciliationPipeline:
    return ReconciliationPipeline(
        doc_source=_FakeDoc(),
        extractor=_FakeExtractor(),
        vision=_FakeVision(),
        config=AppConfig(),
    )


def _block(identity_source: str) -> _GuiaBlock:
    return _GuiaBlock(
        guia_id="ocr_1" if identity_source == "ocr_fallback" else "T112-0001",
        first_page=1,
        source_pages=[1],
        first_page_image=b"\x89PNG",
        lines=[
            MaterialLine(
                description_raw="BARRA",
                description_canonical="barra",
                unidad="TN",
                cantidad=Decimal("1.0"),
                confidence=0.95,
                requires_review=identity_source == "ocr_fallback",
            )
        ],
        registro="232",
        identity_source=identity_source,
        gre_hashqr_url="https://e-factura.sunat.gob.pe/v1/?hashqr=ABC",
    )


def _official() -> OfficialGre:
    gre = OfficialGre.from_identity("T112-0001")
    return gre.model_copy(
        update={
            "fecha_emision": date(2026, 5, 25),
            "fecha_entrega": date(2026, 5, 27),
            "lines": [
                GreLineItem(
                    descripcion='BARRA 1/2" 9M',
                    unidad="TN",
                    cantidad=Decimal("1.0"),
                )
            ],
        }
    )


class TestSunatPreservesRequiresReviewOnOcrFallback:
    """FIX 2: SUNAT line replacement preserves the uncertain-identity flag."""

    def test_ocr_fallback_block_keeps_requires_review_after_sunat(self) -> None:
        """An ocr_fallback block enriched by SUNAT → resulting lines STILL
        requires_review=True.

        RED against pre-fix code (fresh MaterialLines default requires_review=False,
        erasing the C1 flag); GREEN after.
        """
        pipeline = _make_pipeline()
        block = _block("ocr_fallback")

        pipeline._apply_sunat_result(
            block, _official(), _VALID_UNITS, _DOMAIN_UNIT, cast
        )

        assert block.lines, "SUNAT lines must replace the OCR lines"
        assert all(line.requires_review for line in block.lines), (
            "SUNAT-replaced lines on an ocr_fallback block MUST stay requires_review=True "
            "(uncertain-identity flag preserved, not erased)"
        )

    def test_qr_block_stays_not_requires_review_after_sunat(self) -> None:
        """A QR-identified block enriched by SUNAT → lines requires_review=False
        (only ocr_fallback blocks carry the preserved flag)."""
        pipeline = _make_pipeline()
        block = _block("qr")

        pipeline._apply_sunat_result(
            block, _official(), _VALID_UNITS, _DOMAIN_UNIT, cast
        )

        assert block.lines, "SUNAT lines must replace the OCR lines"
        assert not any(line.requires_review for line in block.lines), (
            "SUNAT-replaced lines on a QR-identified block must NOT be requires_review"
        )
