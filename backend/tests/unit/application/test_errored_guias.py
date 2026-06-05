"""Unit tests for the errored_guias side-channel on PipelineResult (REC-EG-001-003).

STRICT TDD: tests B-1 MUST be RED before domain/models.py and pipeline.py are changed.
Tests 1-2 fail because ErroredGuia / errored_guias field do not exist yet.
Tests 3-6 fail because PipelineResult has no errored_guias attribute.
"""

from __future__ import annotations

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
# Fake adapters (mirrors test_block_grouping.py pattern)
# ---------------------------------------------------------------------------


class _FakeDoc:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def page_count(self) -> int:
        return len(self._pages)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return self._pages[idx].get("image", b"\x89PNG")

    def page_text(self, idx: int) -> str | None:
        return self._pages[idx].get("text")


class _FakeExtractor:
    def __init__(self, per_page_lines: list[list[MaterialLine]] | None = None) -> None:
        self._queue = list(per_page_lines or [])

    def extract_declared(self, text: str) -> list[MaterialLine]:
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        if self._queue:
            return self._queue.pop(0)
        return []


class _FakeVision:
    supports_batch: bool = False

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:
        return VisionResult(date=date(2026, 5, 1), confidence=0.99, raw="01/05/2026")


class _StatefulIdentity:
    def __init__(self, sequence: list[GuiaIdentity | None]) -> None:
        self._seq = list(sequence)
        self._idx = 0

    def decode_identity(
        self, image: bytes, page_idx: int | None = None
    ) -> GuiaIdentity | None:
        if self._idx < len(self._seq):
            result = self._seq[self._idx]
        else:
            result = None
        self._idx += 1
        return result


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


def _run_pipeline(
    pages: list[dict[str, Any]],
    identity_seq: list[GuiaIdentity | None] | None = None,
    per_page_lines: list[list[MaterialLine]] | None = None,
    page_to_registro: dict[int, str | None] | None = None,
    tmp_path: Path | None = None,
):
    cfg = AppConfig()
    doc = _FakeDoc(pages)
    extractor = _FakeExtractor(per_page_lines=per_page_lines)
    vision = _FakeVision()
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
# Test 1 — ErroredGuia model fields (B-1, REC-EG-001)
# ---------------------------------------------------------------------------


class TestErroredGuiaModel:
    def test_errored_guia_model_fields(self) -> None:
        """Instantiate ErroredGuia and assert all three fields are accessible.

        FAILS (RED): ErroredGuia does not exist in domain/models.py yet.
        """
        from reconciliation.domain.models import ErroredGuia  # type: ignore[attr-defined]

        eg = ErroredGuia(registro="232", guia_id="T112-0065422", source_pages=[45])
        assert eg.registro == "232"
        assert eg.guia_id == "T112-0065422"
        assert eg.source_pages == [45]

    def test_errored_guia_registro_can_be_none(self) -> None:
        """registro is str | None — must accept None (unresolved section)."""
        from reconciliation.domain.models import ErroredGuia  # type: ignore[attr-defined]

        eg = ErroredGuia(registro=None, guia_id="T112-0065422", source_pages=[45, 46])
        assert eg.registro is None
        assert eg.source_pages == [45, 46]


# ---------------------------------------------------------------------------
# Test 2 — PipelineResult.errored_guias defaults to empty list (B-1, REC-EG-001)
# ---------------------------------------------------------------------------


class TestPipelineResultDefaultEmptyErroredGuias:
    def test_pipeline_result_errored_guias_default_empty(self, tmp_path: Path) -> None:
        """A PipelineResult must carry errored_guias=[] when all guías have lines.

        FAILS (RED): PipelineResult has no errored_guias field yet.
        """
        result = _run_pipeline(
            pages=[_GUIA_PAGE],
            identity_seq=[_identity("T001", "0001")],
            per_page_lines=[[_MAT_LINE]],
            tmp_path=tmp_path,
        )
        # Field must exist and default to empty list when all blocks have lines.
        assert hasattr(result, "errored_guias"), (
            "PipelineResult must have an errored_guias attribute (REC-EG-001)"
        )
        assert result.errored_guias == [], (
            f"errored_guias must be [] when all guías have lines; got: {result.errored_guias}"
        )


# ---------------------------------------------------------------------------
# Test 3 — 0-line guía appears in errored_guias; good guía unaffected (REC-EG-S01)
# ---------------------------------------------------------------------------


class TestZeroLineGuiaAppearsInErroredGuias:
    def test_0_line_block_appears_in_errored_guias(self, tmp_path: Path) -> None:
        """Pipeline with two blocks: one 0-line, one with lines.

        Assert:
        - errored_guias has exactly 1 entry for the 0-line block.
        - The non-empty block is NOT in errored_guias.

        FAILS (RED): errored_guias field does not exist yet.
        """
        qr_ok = _identity("T112", "0065421")
        qr_err = _identity("T112", "0065422")

        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[qr_ok, qr_err],
            per_page_lines=[[_MAT_LINE], []],  # second block has 0 lines
            page_to_registro={0: "232", 1: "232"},
            tmp_path=tmp_path,
        )

        assert hasattr(result, "errored_guias"), (
            "PipelineResult must have errored_guias attribute"
        )
        assert len(result.errored_guias) == 1, (
            f"Expected 1 errored guía (the 0-line block); got {len(result.errored_guias)}: "
            f"{result.errored_guias}"
        )
        entry = result.errored_guias[0]
        assert entry.guia_id == "T112-0065422", (
            f"Errored guía must be T112-0065422; got {entry.guia_id!r}"
        )
        assert entry.registro == "232"
        assert 1 in entry.source_pages

        # Good guía must NOT be in errored_guias
        errored_ids = {e.guia_id for e in result.errored_guias}
        assert "T112-0065421" not in errored_ids, (
            "Non-empty guía T112-0065421 must NOT appear in errored_guias"
        )


# ---------------------------------------------------------------------------
# Test 4 — Additive-only invariant: correct guía rows unchanged (REC-EG-S02)
# ---------------------------------------------------------------------------


class TestErroredGuiasAdditiveOnlyInvariant:
    def test_errored_guias_additive_only_invariant(self, tmp_path: Path) -> None:
        """The errored_guias side-channel MUST NOT alter key/status/delta/qty for correct rows.

        Run with one good guía (T112-0065421, 100 KG) and one 0-line guía (T112-0065422).
        Assert that:
        - T112-0065421's row summed_qty == 100 (its own contribution, unchanged by side-channel).
        - T112-0065421 NOT in errored_guias.
        - T112-0065422 IS in errored_guias (0-line block surfaced).

        The additive-only invariant: the side-channel does not touch the good row's
        key, status, delta, or summed_qty. No second run is needed — the side-channel
        must be isolated from the reconcile path.

        FAILS (RED): errored_guias field does not exist yet.
        """
        qr_ok = _identity("T112", "0065421")
        qr_err = _identity("T112", "0065422")

        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[qr_ok, qr_err],
            per_page_lines=[[_MAT_LINE], []],  # second block: 0 lines
            page_to_registro={0: "232", 1: "232"},
            tmp_path=tmp_path,
        )

        # errored_guias side-channel must capture the 0-line block
        assert hasattr(result, "errored_guias")
        assert len(result.errored_guias) == 1
        assert result.errored_guias[0].guia_id == "T112-0065422"

        # Good guía must produce a reconciliation row with correct qty
        rows_by_guia = {r.guias[0].guia_id: r for r in result.rows if r.guias}
        assert "T112-0065421" in rows_by_guia, (
            "T112-0065421 must still produce a reconciliation row even when another guía is errored"
        )
        good_row = rows_by_guia["T112-0065421"]

        # summed_qty for the good guía must equal its own single line contribution
        assert good_row.summed_qty == Decimal("100"), (
            f"summed_qty must be 100 (good guía's own qty, unaffected by side-channel); "
            f"got {good_row.summed_qty}"
        )

        # The good guía must NOT appear in errored_guias
        errored_ids = {e.guia_id for e in result.errored_guias}
        assert "T112-0065421" not in errored_ids, (
            "Good guía T112-0065421 must NOT appear in errored_guias"
        )


# ---------------------------------------------------------------------------
# Test 5 — errored_guias empty when all blocks have lines (REC-EG-S03)
# ---------------------------------------------------------------------------


class TestErroredGuiasEmptyWhenAllHaveLines:
    def test_errored_guias_empty_when_all_blocks_have_lines(self, tmp_path: Path) -> None:
        """All blocks ≥1 line → errored_guias == [] (empty list, not null).

        FAILS (RED): errored_guias field does not exist yet.
        """
        qr1 = _identity("T001", "0001")
        qr2 = _identity("T001", "0002")
        qr3 = _identity("T001", "0003")

        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[qr1, qr2, qr3],
            per_page_lines=[[_MAT_LINE], [_MAT_LINE], [_MAT_LINE]],
            tmp_path=tmp_path,
        )

        assert hasattr(result, "errored_guias")
        assert result.errored_guias == [], (
            f"errored_guias must be [] when all blocks have lines; got: {result.errored_guias}"
        )
        assert result.errored_guias is not None, "errored_guias must never be None"


# ---------------------------------------------------------------------------
# Test 6 — Multiple errored guías across registros (REC-EG-S04)
# ---------------------------------------------------------------------------


class TestMultipleErroredGuiasAcrossRegistros:
    def test_multiple_errored_guias_across_registros(self, tmp_path: Path) -> None:
        """registro 227: 1 errored; registro 232: 2 errored → len(errored_guias) == 3.

        Each entry must have correct registro/guia_id/source_pages.

        FAILS (RED): errored_guias field does not exist yet.
        """
        # registro 227: 1 guía, 0 lines
        qr_227_a = _identity("T227", "0000001")

        # registro 232: 2 guías, both 0 lines; 1 good guía
        qr_232_ok = _identity("T232", "0000001")
        qr_232_err_a = _identity("T232", "0000002")
        qr_232_err_b = _identity("T232", "0000003")

        result = _run_pipeline(
            pages=[_GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE, _GUIA_PAGE],
            identity_seq=[qr_227_a, qr_232_ok, qr_232_err_a, qr_232_err_b],
            per_page_lines=[[], [_MAT_LINE], [], []],
            page_to_registro={0: "227", 1: "232", 2: "232", 3: "232"},
            tmp_path=tmp_path,
        )

        assert hasattr(result, "errored_guias")
        assert len(result.errored_guias) == 3, (
            f"Expected 3 errored guías (1 in reg 227, 2 in reg 232); "
            f"got {len(result.errored_guias)}: {[(e.registro, e.guia_id) for e in result.errored_guias]}"
        )

        errored_ids = {e.guia_id for e in result.errored_guias}
        assert "T227-0000001" in errored_ids
        assert "T232-0000002" in errored_ids
        assert "T232-0000003" in errored_ids
        # Good guía must NOT appear
        assert "T232-0000001" not in errored_ids

        # Check registro field is correctly propagated
        for entry in result.errored_guias:
            assert entry.registro is not None, f"registro must be set; got None for {entry.guia_id}"
            assert isinstance(entry.source_pages, list)
            assert len(entry.source_pages) >= 1
