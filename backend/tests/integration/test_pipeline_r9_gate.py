"""r9 real-data gate — fecha-divergence assertions + regression guard (R9.8).

Items 1-3 are fast regression guards (no PDF) — they always run.
Item 4 is the trusted real-PDF e2e gate — marked slow/e2e and skipped unless
``CTR_PDF_PATH`` points at the real CTR PDF (per HANDOFF.md §4: never trust green
unit tests; the real pipeline once passed unit tests while broken).

Spec refs: FDR-S01, FDR-S03, FDR-S04, FDR-S09, FDR-S19, ADR-7.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from reconciliation.application.config import AppConfig, StampCropConfig
from reconciliation.domain.date_divergence import check_fecha_divergence
from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
    Registro,
)
from reconciliation.domain.reconciliation import ReconciliationService


# ---------------------------------------------------------------------------
# Item 1 — pure-domain divergence predicate (fast)
# ---------------------------------------------------------------------------


class TestR9DivergencePredicateGate:
    def test_identical_dates_no_divergence(self) -> None:
        assert check_fecha_divergence(date(2026, 5, 28), date(2026, 5, 28)).diverges is False

    def test_year_only_difference_not_divergent(self) -> None:
        """FDR-S04 (CRITICAL): year-inference asymmetry must not flag a divergence."""
        assert check_fecha_divergence(date(2026, 5, 28), date(2025, 5, 28)).diverges is False

    def test_day_month_difference_diverges(self) -> None:
        r = check_fecha_divergence(date(2026, 5, 28), date(2026, 4, 15))
        assert r.diverges is True
        assert r.reason == "fecha_divergence"

    def test_null_declared_not_divergent(self) -> None:
        assert check_fecha_divergence(None, date(2026, 5, 28)).diverges is False


# ---------------------------------------------------------------------------
# Item 2 — ReconciliationService simulation with divergence (fast, no PDF)
# ---------------------------------------------------------------------------


class TestR9ReconcilerDivergenceGate:
    def _line(self, qty: str) -> MaterialLine:
        return MaterialLine(
            description_raw="BARRA A615 G60 1/2\"",
            description_canonical="barra a615 g60 1/2\"",
            unidad="TN",
            cantidad=Decimal(qty),
            source_page=10,
        )

    def _guia(self, gid: str, fecha: date, qty: str, page: int) -> GuiaDeRemision:
        return GuiaDeRemision(
            guia_id=gid,
            registro="232",
            fecha=fecha,
            lines=[
                MaterialLine(
                    description_raw="BARRA A615 G60 1/2\"",
                    description_canonical="barra a615 g60 1/2\"",
                    unidad="TN",
                    cantidad=Decimal(qty),
                    source_page=page,
                )
            ],
            source_pages=[page],
        )

    def test_divergent_guia_flagged_status_unchanged(self) -> None:
        """FDR-S09/S19: divergence flags the guía + requires_review; status is qty-driven."""
        declared = [
            Registro(
                numero="232",
                fecha_declarada=date(2026, 5, 28),
                fecha_declarada_handwritten=date(2026, 5, 28),
                fecha_declarada_confidence=0.92,
                declared_lines=[self._line("4.124")],
            )
        ]
        guias = [
            self._guia("T009-MATCH", date(2026, 5, 28), "2.062", 5),
            self._guia("T009-DIVERGENT", date(2026, 4, 15), "2.062", 6),
        ]
        rows = ReconciliationService().reconcile(declared, guias)
        assert len(rows) == 1
        row = rows[0]

        by_id = {c.guia_id: c for c in row.guias}
        assert by_id["T009-MATCH"].fecha_divergence is False
        assert by_id["T009-DIVERGENT"].fecha_divergence is True
        assert by_id["T009-DIVERGENT"].divergence_reason == "fecha_divergence"

        assert row.has_fecha_divergence is True
        # Status is driven by quantities ONLY (2.062 + 2.062 == 4.124 → MATCH).
        assert row.status == "MATCH"
        assert row.summed_qty == Decimal("4.124")
        # Divergence OR-sets requires_review.
        assert row.requires_review is True

    def test_year_only_divergence_not_flagged(self) -> None:
        """FDR-S04 end-to-end through the reconciler."""
        declared = [
            Registro(
                numero="232",
                fecha_declarada=date(2026, 5, 28),
                fecha_declarada_handwritten=date(2026, 5, 28),
                fecha_declarada_confidence=0.92,
                declared_lines=[self._line("2.000")],
            )
        ]
        guias = [self._guia("T009-YEAR", date(2025, 5, 28), "2.000", 5)]
        rows = ReconciliationService().reconcile(declared, guias)
        assert rows[0].guias[0].fecha_divergence is False
        assert rows[0].has_fecha_divergence is False


# ---------------------------------------------------------------------------
# Item 3 — pipeline configuration guard (fast, no PDF)
# ---------------------------------------------------------------------------


class TestR9PipelineConfigGate:
    def test_protocolo_crop_present_and_calibrated_enabled_by_default(self) -> None:
        # R10.9 calibration: the Protocolo "Fecha:" crop is calibrated to the
        # upper-right header (0.60, 0.14, 1.00, 0.22) so it targets only the
        # Registro N° + handwritten Fecha rows. This is a non-degenerate box, so
        # ``enabled`` is True by default (the reception-date-authority skill
        # mandates this crop).
        cfg = AppConfig()
        assert isinstance(cfg.vision.protocolo_crop, StampCropConfig)
        assert cfg.vision.protocolo_crop.enabled is True
        assert cfg.vision.protocolo_crop.x0 == 0.60
        assert cfg.vision.protocolo_crop.y0 == 0.14
        assert cfg.vision.protocolo_crop.x1 == 1.00
        assert cfg.vision.protocolo_crop.y1 == 0.22

    def test_stamp_crop_unaffected(self) -> None:
        """No regression to the R7 guía stamp crop."""
        cfg = AppConfig()
        assert cfg.vision.stamp_crop.enabled is True


# ---------------------------------------------------------------------------
# Item 4 — full real-PDF e2e gate (trusted gate; skipped without the asset)
# ---------------------------------------------------------------------------

_PDF_ASSET_VAR = "CTR_PDF_PATH"
_PDF_PATH = Path(os.environ.get(_PDF_ASSET_VAR, "")) if os.environ.get(_PDF_ASSET_VAR) else None


@pytest.mark.slow
@pytest.mark.e2e
@pytest.mark.skipif(
    _PDF_PATH is None or not _PDF_PATH.exists(),
    reason=(
        f"Real PDF asset not available. "
        f"Set {_PDF_ASSET_VAR}=/path/to/CTR-PLC01-FR001.pdf to run this gate."
    ),
)
class TestR9RealPDFGate:
    """Full pipeline e2e gate against the real CTR PDF (R9.8 item 4).

    MUST PASS before declaring r9-fecha-divergence-review complete.
    Date divergence is an ADDITIVE side-channel: it must NOT change any MATCH
    status that R8 produced (FDR-S09).
    """

    @pytest.fixture(scope="class")
    def pipeline_result(self, tmp_path_factory):
        from reconciliation.infrastructure.container import build_pipeline

        tmp_path = tmp_path_factory.mktemp("r9_gate")
        config = AppConfig(output_dir=tmp_path / "runs")
        pipeline, ctx, _ = build_pipeline(_PDF_PATH, config)
        return pipeline.run(ctx)

    def test_registro_232_protocolo_page_propagated(self, pipeline_result) -> None:
        """R9.1: Registro 232 carries a concrete Protocolo page index (not None)."""
        reg_232 = [r for r in pipeline_result.declared if r.numero == "232"]
        assert reg_232, "Registro 232 not found in declared output."
        assert reg_232[0].protocolo_page is not None
        assert isinstance(reg_232[0].protocolo_page, int)

    def test_registro_232_declared_date_read(self, pipeline_result) -> None:
        """ADR-7: vision stage ran — either a confident handwritten date or a
        recorded confidence (fail-closed). Both outcomes are valid."""
        reg_232 = [r for r in pipeline_result.declared if r.numero == "232"][0]
        if reg_232.fecha_declarada_handwritten is not None:
            assert reg_232.fecha_declarada_confidence is not None
            assert reg_232.fecha_declarada_confidence >= 0.85
        else:
            # Low-confidence path: confidence recorded, no baseline asserted.
            assert reg_232.fecha_authoritative == reg_232.fecha_declarada

    def test_at_least_one_match_row_regression(self, pipeline_result) -> None:
        """R8 regression guard: matching still resolves at least one MATCH."""
        match_rows = [r for r in pipeline_result.rows if r.status == "MATCH"]
        assert len(match_rows) > 0

    def test_4252_family_row_still_match(self, pipeline_result) -> None:
        """R8 regression: registro=232 1/2\" TN still MATCHes at 4.124 TN deterministically."""
        target = [
            r for r in pipeline_result.rows
            if r.registro == "232" and '1/2"' in r.material_canonical
            and r.unidad == "TN" and r.status == "MATCH"
        ]
        assert len(target) >= 1
        row = target[0]
        assert row.summed_qty == Decimal("4.124")
        assert row.match_method == "deterministic"

    def test_divergence_flags_imply_requires_review(self, pipeline_result) -> None:
        """FDR-S09: any row with a divergence must also be requires_review=True."""
        for row in pipeline_result.rows:
            if row.has_fecha_divergence:
                assert row.requires_review is True
