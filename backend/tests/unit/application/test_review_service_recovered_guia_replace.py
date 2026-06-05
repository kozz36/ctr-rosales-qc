"""REV-R05 fix: add_recovered_guia REPLACE-semantics against the REAL precondition.

Root cause of the REINTENTAR non-functional bug: the errored 0-line guía is ALREADY
present in ``_guias`` (the pipeline keeps 0-line blocks as 0-line GuiaDeRemision and
persists them to the extraction cache; ``errored_guias`` is a PARALLEL side-channel
listing those SAME guia_ids).  The old idempotency early-return
(``if any(g.guia_id == guia.guia_id ...): return``) therefore short-circuited every
real recovery: the 0-line placeholder was never replaced, never removed from
``_errored_guias``, never re-reconciled.

These tests construct the REAL precondition: ``_guias`` CONTAINS a 0-line placeholder
with the same guia_id as the ErroredGuia.  They are RED against the early-return.

Strict-TDD: failing tests written BEFORE the fix (RED → GREEN).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from reconciliation.application.review_service import ReviewService
from reconciliation.application.run_context import RunContext
from reconciliation.domain.models import (
    ErroredGuia,
    GuiaDeRemision,
    MaterialLine,
    Registro,
)
from reconciliation.domain.reconciliation import ReconciliationService


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_DESC = 'BARRA AG615/A706 G60 1/2" x 9M'


def _make_line(
    desc: str = _DESC,
    qty: str = "4.124",
    unit: str = "TN",
) -> MaterialLine:
    return MaterialLine(
        description_raw=desc,
        description_canonical=desc,
        unidad=unit,  # type: ignore[arg-type]
        cantidad=Decimal(qty),
        confidence=1.0,
        source_page=4,
        requires_review=True,
    )


def _make_declared(numero: str = "232") -> Registro:
    """Declared registro expecting 4.124 TN of the same canonical material."""
    return Registro(
        numero=numero,
        fecha_declarada=date(2026, 5, 28),
        declared_lines=[_make_line()],
    )


def _make_placeholder_guia(
    guia_id: str = "T009-0741770",
    registro: str = "232",
) -> GuiaDeRemision:
    """The 0-line placeholder the pipeline persists for an errored block."""
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=date(2026, 5, 28),
        lines=[],  # 0-line — the REAL precondition that triggered the bug
        source_pages=[4],
    )


def _make_errored(
    guia_id: str = "T009-0741770",
    registro: str = "232",
) -> ErroredGuia:
    return ErroredGuia(registro=registro, guia_id=guia_id, source_pages=[4])


def _make_recovered_guia(
    guia_id: str = "T009-0741770",
    registro: str = "232",
) -> GuiaDeRemision:
    """The with-lines guía produced by ReprocessService (same guia_id as the errored one)."""
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=date(2026, 5, 28),
        fecha_entrega=date(2026, 5, 28),
        lines=[_make_line()],
        source_pages=[4],
        identity_source="qr",
    )


def _make_ctx(tmp_path: Path, run_id: str = "test-replace") -> RunContext:
    ctx = RunContext(
        pdf_path=tmp_path / "input.pdf",
        output_base=tmp_path / "runs",
        run_id=run_id,
    )
    ctx.write_review_sidecar({"edits": [], "audit_trail": []})
    return ctx


def _build_service(tmp_path: Path) -> tuple[ReviewService, RunContext]:
    """ReviewService whose _guias CONTAINS the 0-line placeholder (REAL precondition)."""
    ctx = _make_ctx(tmp_path)
    declared = [_make_declared()]
    guias = [_make_placeholder_guia()]  # 0-line placeholder IS in _guias
    errored = [_make_errored()]
    reconciler = ReconciliationService()
    rows = reconciler.reconcile(declared, guias)
    service = ReviewService(
        declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
    )
    return service, ctx


# ---------------------------------------------------------------------------
# Test 1 — REPLACE the 0-line placeholder (RED against the early-return)
# ---------------------------------------------------------------------------


class TestAddRecoveredGuiaReplace:
    def test_placeholder_replaced_with_lines_version(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)

        # Sanity: precondition is the placeholder (0 lines) in _guias.
        before = next(g for g in service.guias if g.guia_id == "T009-0741770")
        assert len(before.lines) == 0

        service.add_recovered_guia(_make_recovered_guia())

        matching = [g for g in service.guias if g.guia_id == "T009-0741770"]
        assert len(matching) == 1, "must REPLACE, not append a duplicate"
        assert len(matching[0].lines) == 1, "the with-lines version must win"

    def test_errored_entry_removed(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.add_recovered_guia(_make_recovered_guia())
        errored_ids = {e.guia_id for e in service.errored_guias}
        assert "T009-0741770" not in errored_ids

    def test_reconciliation_row_reflects_recovered_qty(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        # Before recovery the placeholder contributes 0 → MISMATCH/GUIA_MISSING.
        service.add_recovered_guia(_make_recovered_guia())

        row = next(
            r
            for r in service.rows
            if r.registro == "232"
            and r.unidad == "TN"
            and r.material_canonical == _DESC
        )
        assert row.summed_qty == Decimal("4.124")
        assert row.status == "MATCH"

    def test_recovered_lines_require_review(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.add_recovered_guia(_make_recovered_guia())
        recovered = next(g for g in service.guias if g.guia_id == "T009-0741770")
        assert len(recovered.lines) == 1, "the with-lines version must replace the placeholder"
        assert all(line.requires_review for line in recovered.lines)


# ---------------------------------------------------------------------------
# Test 2 — TRUE idempotency: a SECOND call when already recovered is a no-op
# ---------------------------------------------------------------------------


class TestAddRecoveredGuiaTrueIdempotency:
    def test_second_call_when_already_recovered_is_noop(self, tmp_path: Path) -> None:
        service, ctx = _build_service(tmp_path)
        service.add_recovered_guia(_make_recovered_guia())

        rows_after_first = service.rows
        events_after_first = [
            e for e in ctx.read_review_sidecar().get("edits", [])
            if e.get("kind") == "recovered_guia"
        ]
        assert len(events_after_first) == 1

        # Second call with the same (already recovered) guia_id → no-op.
        service.add_recovered_guia(_make_recovered_guia())

        matching = [g for g in service.guias if g.guia_id == "T009-0741770"]
        assert len(matching) == 1
        assert len(matching[0].lines) == 1
        # No duplicate event emitted.
        events_after_second = [
            e for e in ctx.read_review_sidecar().get("edits", [])
            if e.get("kind") == "recovered_guia"
        ]
        assert len(events_after_second) == 1
        # Rows unchanged.
        assert [r.model_dump() for r in service.rows] == [
            r.model_dump() for r in rows_after_first
        ]


# ---------------------------------------------------------------------------
# Test 3 — Sidecar round-trip from the REAL precondition (no re-fetch)
# ---------------------------------------------------------------------------


class TestAddRecoveredGuiaSidecarRoundTripRealPrecondition:
    def test_restore_replays_recovery_over_placeholder(self, tmp_path: Path) -> None:
        service, ctx = _build_service(tmp_path)
        service.add_recovered_guia(_make_recovered_guia())

        # Restart: the cache still holds the 0-line placeholder + the ErroredGuia.
        declared = [_make_declared()]
        guias = [_make_placeholder_guia()]
        errored = [_make_errored()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        svc2 = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=errored,
        )

        # The recovered guía (with lines) is present exactly once; placeholder gone.
        matching = [g for g in svc2.guias if g.guia_id == "T009-0741770"]
        assert len(matching) == 1
        assert len(matching[0].lines) == 1
        # Errored reduced.
        assert "T009-0741770" not in {e.guia_id for e in svc2.errored_guias}
        # Row reflects recovered qty without any re-fetch.
        row = next(
            r
            for r in svc2.rows
            if r.registro == "232" and r.unidad == "TN"
        )
        assert row.summed_qty == Decimal("4.124")
        assert row.status == "MATCH"
