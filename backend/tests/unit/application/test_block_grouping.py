"""Unit tests for S1.5 — multi-page guía block grouping (EXT-015/016/017/018).

Tests the pipeline's _stage_assemble_blocks logic indirectly via ReconciliationPipeline.run()
using configurable fake adapters.  No external deps (pyzbar/zxing-cpp) required.

Scenarios covered:
  EXT-S15: 3 consecutive guía pages, same section, first has QR → single block, all lines merged.
  EXT-S16: page 2 has new QR with different guia_id → two blocks.
  EXT-S17: section boundary separates consecutive guía pages → two blocks.
  EXT-S18: 10 guía pages → no GuiaDeRemision.guia_id matches guia_page_\\d+ pattern.
  OCR fallback: QR decode returns None → identity_source="ocr_fallback".
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.application.run_context import RunContext
from reconciliation.domain.models import GuiaIdentity, MaterialLine, VisionResult


# ---------------------------------------------------------------------------
# Fake adapters (no external deps)
# ---------------------------------------------------------------------------


class FakeDocumentSource:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def page_count(self) -> int:
        return len(self._pages)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return self._pages[idx].get("image", b"\x89PNG")

    def page_text(self, idx: int) -> str | None:
        return self._pages[idx].get("text")


class FakeExtractor:
    """Returns a configurable list of lines per call (FIFO queue)."""

    def __init__(self, per_page_lines: list[list[MaterialLine]] | None = None) -> None:
        self._queue = list(per_page_lines or [])

    def extract_declared(self, text: str) -> list[MaterialLine]:  # pragma: no cover
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        if self._queue:
            return self._queue.pop(0)
        return []


class FakeVision:
    supports_batch: bool = False

    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult:
        return VisionResult(date=date(2026, 5, 1), confidence=0.99, raw="01/05/2026")

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:  # pragma: no cover
        return [self.read_handwritten_date(img) for img in images]


class _StatefulIdentity:
    """Fake IdentityExtractionPort with per-call configuration.

    ``sequence`` is a list of GuiaIdentity | None values returned in order;
    when exhausted, returns None.
    """

    def __init__(self, sequence: list[GuiaIdentity | None]) -> None:
        self._seq = list(sequence)
        self._idx = 0

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
        if self._idx < len(self._seq):
            result = self._seq[self._idx]
        else:
            result = None
        self._idx += 1
        return result


def _identity(serie: str, numero: str) -> GuiaIdentity:
    return GuiaIdentity(
        serie=serie,
        numero=numero,
        ruc_emisor="12345678901",
        ruc_receptor="10987654321",
        tipo="09",
        hashqr_url=None,
        confidence=1.0,
    )


_GUIA_TEXT = (
    "PTR001-TORRE ROSALES\n"
    "Informe de detalle del formulario\n"
    "GUIA DE REMISION\n"
)

_GUIA_PAGE = {"text": _GUIA_TEXT, "image": b"\x89PNG"}

_MAT_LINE = MaterialLine(
    description_raw="BARRA 3/8",
    description_canonical="barra 3/8",
    unidad="KG",
    cantidad=Decimal("100"),
    confidence=0.95,
)


def _run_pipeline(
    pages: list[dict[str, Any]],
    identity_seq: list[GuiaIdentity | None] | None = None,
    per_page_lines: list[list[MaterialLine]] | None = None,
    page_to_registro: dict[int, str | None] | None = None,
    tmp_path: Path | None = None,
):
    cfg = AppConfig()
    doc = FakeDocumentSource(pages)
    extractor = FakeExtractor(per_page_lines=per_page_lines)
    vision = FakeVision()
    identity = _StatefulIdentity(identity_seq) if identity_seq is not None else None

    pipeline = ReconciliationPipeline(
        doc_source=doc,
        extractor=extractor,
        vision=vision,
        config=cfg,
        page_to_registro=page_to_registro or {},
        identity=identity,
    )
    base = tmp_path or Path(".")
    ctx = RunContext(pdf_path=base / "in.pdf", output_base=base / "runs")
    return pipeline.run(ctx)


# ---------------------------------------------------------------------------
# EXT-S15: 3 consecutive guía pages, same section, first has QR → single block
# ---------------------------------------------------------------------------


class TestEXTS15SingleBlockSameQr:
    def test_three_pages_same_qr_form_one_block(self, tmp_path: Path) -> None:
        """3 consecutive GUIA pages, first has QR T001-0001; pages 2+3 have None → 1 block."""
        qr = _identity("T001", "0001")
        identity_seq: list[GuiaIdentity | None] = [qr, None, None]

        line_a = _MAT_LINE.model_copy(update={"cantidad": Decimal("100")})
        line_b = _MAT_LINE.model_copy(update={"cantidad": Decimal("200")})
        line_c = _MAT_LINE.model_copy(update={"cantidad": Decimal("50")})

        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=identity_seq,
            per_page_lines=[[line_a], [line_b], [line_c]],
            tmp_path=tmp_path,
        )
        assert len(result.guias) == 1, (
            f"Expected 1 block; got {len(result.guias)}: {[g.guia_id for g in result.guias]}"
        )
        guia = result.guias[0]
        assert guia.guia_id == "T001-0001"
        assert guia.identity_source == "qr"
        # All 3 pages' lines merged
        assert len(guia.lines) == 3
        total = sum(l.cantidad for l in guia.lines)
        assert total == Decimal("350")

    def test_three_pages_source_pages_cover_all(self, tmp_path: Path) -> None:
        """source_pages on the block covers pages 0, 1, 2."""
        qr = _identity("T001", "0001")
        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[qr, None, None],
            per_page_lines=[[_MAT_LINE], [_MAT_LINE], [_MAT_LINE]],
            tmp_path=tmp_path,
        )
        assert sorted(result.guias[0].source_pages) == [0, 1, 2]


# ---------------------------------------------------------------------------
# EXT-S16: page 2 has new QR → two blocks
# ---------------------------------------------------------------------------


class TestEXTS16TwoBlocksDifferentQr:
    def test_new_qr_on_page2_starts_second_block(self, tmp_path: Path) -> None:
        """Page 0: QR T001-0001. Page 1: None (continuation). Page 2: new QR T001-0002 → split."""
        qr1 = _identity("T001", "0001")
        qr2 = _identity("T001", "0002")
        identity_seq: list[GuiaIdentity | None] = [qr1, None, qr2]

        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=identity_seq,
            per_page_lines=[[_MAT_LINE], [_MAT_LINE], [_MAT_LINE]],
            tmp_path=tmp_path,
        )
        assert len(result.guias) == 2, (
            f"Expected 2 blocks; got {len(result.guias)}: {[g.guia_id for g in result.guias]}"
        )
        ids = {g.guia_id for g in result.guias}
        assert ids == {"T001-0001", "T001-0002"}

    def test_first_block_has_pages_0_and_1(self, tmp_path: Path) -> None:
        """First block (T001-0001) covers pages 0 and 1 (continuation)."""
        qr1 = _identity("T001", "0001")
        qr2 = _identity("T001", "0002")
        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[qr1, None, qr2],
            per_page_lines=[[_MAT_LINE], [_MAT_LINE], [_MAT_LINE]],
            tmp_path=tmp_path,
        )
        block1 = next(g for g in result.guias if g.guia_id == "T001-0001")
        assert 0 in block1.source_pages
        assert 1 in block1.source_pages

    def test_second_block_has_page_2_only(self, tmp_path: Path) -> None:
        """Second block (T001-0002) covers page 2 only."""
        qr1 = _identity("T001", "0001")
        qr2 = _identity("T001", "0002")
        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[qr1, None, qr2],
            per_page_lines=[[_MAT_LINE], [_MAT_LINE], [_MAT_LINE]],
            tmp_path=tmp_path,
        )
        block2 = next(g for g in result.guias if g.guia_id == "T001-0002")
        assert block2.source_pages == [2]


# ---------------------------------------------------------------------------
# EXT-S17: section boundary separates consecutive guía pages → two blocks
# ---------------------------------------------------------------------------


class TestEXTS17SectionBoundary:
    def test_section_boundary_starts_new_block(self, tmp_path: Path) -> None:
        """Pages 0+1 in registro '232'; page 2 in registro '231' → split at boundary."""
        # No QR on any page: OCR fallback. Section boundary (different registro) triggers split.
        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[None, None, None],
            per_page_lines=[[_MAT_LINE], [_MAT_LINE], [_MAT_LINE]],
            page_to_registro={0: "232", 1: "232", 2: "231"},
            tmp_path=tmp_path,
        )
        # Pages 0+1 → one block (registro='232'), page 2 → second block (registro='231')
        assert len(result.guias) == 2, (
            f"Expected 2 blocks from section boundary; got {len(result.guias)}"
        )
        registros = {g.registro for g in result.guias}
        assert registros == {"232", "231"}

    def test_first_block_covers_section_232(self, tmp_path: Path) -> None:
        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[None, None, None],
            per_page_lines=[[_MAT_LINE], [_MAT_LINE], [_MAT_LINE]],
            page_to_registro={0: "232", 1: "232", 2: "231"},
            tmp_path=tmp_path,
        )
        block_232 = next(g for g in result.guias if g.registro == "232")
        assert sorted(block_232.source_pages) == [0, 1]


# ---------------------------------------------------------------------------
# EXT-S18: 10 guía pages → no guia_page_\\d+ pattern
# ---------------------------------------------------------------------------


class TestEXTS18NoGuiaPagePattern:
    _GUIA_PAGE_PATTERN = re.compile(r"^guia_page_\d+$")

    def test_ten_pages_no_guia_page_id(self, tmp_path: Path) -> None:
        """10 GUIA pages processed → no GuiaDeRemision.guia_id matches guia_page_N."""
        result = _run_pipeline(
            pages=[_GUIA_PAGE] * 10,
            identity_seq=[None] * 10,
            per_page_lines=[[_MAT_LINE]] * 10,
            tmp_path=tmp_path,
        )
        for guia in result.guias:
            assert not self._GUIA_PAGE_PATTERN.match(guia.guia_id), (
                f"guia_id={guia.guia_id!r} matches the forbidden guia_page_N pattern"
            )

    def test_ten_pages_with_qr_no_guia_page_id(self, tmp_path: Path) -> None:
        """10 GUIA pages, each with a unique QR → 10 blocks, none with guia_page_N id."""
        identity_seq = [_identity("T001", str(i)) for i in range(10)]
        result = _run_pipeline(
            pages=[_GUIA_PAGE] * 10,
            identity_seq=identity_seq,
            per_page_lines=[[_MAT_LINE]] * 10,
            tmp_path=tmp_path,
        )
        assert len(result.guias) == 10
        for guia in result.guias:
            assert not self._GUIA_PAGE_PATTERN.match(guia.guia_id), (
                f"guia_id={guia.guia_id!r} matches the forbidden guia_page_N pattern"
            )


# ---------------------------------------------------------------------------
# OCR fallback: decode returns None → identity_source="ocr_fallback"
# ---------------------------------------------------------------------------


class TestOcrFallback:
    def test_none_decode_sets_ocr_fallback(self, tmp_path: Path) -> None:
        """When decode_identity returns None → identity_source='ocr_fallback'."""
        result = _run_pipeline(
            pages=[_GUIA_PAGE],
            identity_seq=[None],
            per_page_lines=[[_MAT_LINE]],
            tmp_path=tmp_path,
        )
        assert len(result.guias) == 1
        assert result.guias[0].identity_source == "ocr_fallback"

    def test_qr_decode_sets_qr_source(self, tmp_path: Path) -> None:
        """When decode_identity returns a GuiaIdentity → identity_source='qr'."""
        qr = _identity("T009", "0741770")
        result = _run_pipeline(
            pages=[_GUIA_PAGE],
            identity_seq=[qr],
            per_page_lines=[[_MAT_LINE]],
            tmp_path=tmp_path,
        )
        assert result.guias[0].identity_source == "qr"
        assert result.guias[0].guia_id == "T009-0741770"

    def test_no_identity_adapter_uses_fallback(self, tmp_path: Path) -> None:
        """When no identity adapter is wired → OCR fallback (identity_source='ocr_fallback')."""
        result = _run_pipeline(
            pages=[_GUIA_PAGE],
            identity_seq=None,  # no adapter
            per_page_lines=[[_MAT_LINE]],
            tmp_path=tmp_path,
        )
        assert result.guias[0].identity_source == "ocr_fallback"

    def test_ocr_fallback_guia_id_unique_per_page_without_qr(self, tmp_path: Path) -> None:
        """Two pages with None decode but different registros → two separate blocks with unique ids."""
        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[None, None],
            per_page_lines=[[_MAT_LINE], [_MAT_LINE]],
            page_to_registro={0: "232", 1: "231"},  # section boundary → 2 blocks
            tmp_path=tmp_path,
        )
        assert len(result.guias) == 2
        ids = [g.guia_id for g in result.guias]
        assert len(set(ids)) == 2, f"Expected unique guia_ids; got {ids}"


# ---------------------------------------------------------------------------
# Block identity field propagation
# ---------------------------------------------------------------------------


class TestIdentityPropagation:
    def test_ruc_emisor_propagated_from_first_page(self, tmp_path: Path) -> None:
        """RUC from first page's QR appears on the assembled GuiaDeRemision."""
        qr = GuiaIdentity(
            serie="T009",
            numero="0741770",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="09",
            hashqr_url=None,
            confidence=1.0,
        )
        result = _run_pipeline(
            pages=[_GUIA_PAGE],
            identity_seq=[qr],
            per_page_lines=[[_MAT_LINE]],
            tmp_path=tmp_path,
        )
        guia = result.guias[0]
        assert guia.ruc_emisor == "20370146994"
        assert guia.ruc_receptor == "20613231871"
        assert guia.identity_confidence == 1.0

    def test_first_page_index_stored(self, tmp_path: Path) -> None:
        """first_page on the block reflects the 0-based page index of the block start."""
        qr1 = _identity("T001", "0001")
        qr2 = _identity("T001", "0002")
        # Page 0: DECLARED (not GUIA); pages 1, 2, 3: GUIA with two blocks
        _DECLARED_TEXT = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "GUIA DE REMISION\n"  # force GUIA classification for simplicity
        )
        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[qr1, None, qr2],
            per_page_lines=[[_MAT_LINE], [_MAT_LINE], [_MAT_LINE]],
            tmp_path=tmp_path,
        )
        block1 = next(g for g in result.guias if g.guia_id == "T001-0001")
        assert block1.first_page == 0
        block2 = next(g for g in result.guias if g.guia_id == "T001-0002")
        assert block2.first_page == 2
