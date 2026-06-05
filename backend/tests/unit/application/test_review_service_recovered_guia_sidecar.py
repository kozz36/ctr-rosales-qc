"""T-4: sidecar persistence and replay for the recovered_guia event (REV-R06).

Verifies that after a restart (restore_from_sidecar), a recovered guía is replayed
correctly: removed from errored_guias and added to the guía list without re-fetching.

Strict-TDD: failing tests written BEFORE implementation.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from reconciliation.application.review_service import ReviewService
from reconciliation.application.run_context import RunContext
from reconciliation.domain.models import (
    ErroredGuia,
    GuiaDeRemision,
    MaterialLine,
    ReconciliationRow,
    Registro,
)
from reconciliation.domain.reconciliation import ReconciliationService


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_line(
    desc: str = "acero corrugado",
    qty: str = "30",
    unit: str = "KG",
) -> MaterialLine:
    return MaterialLine(
        description_raw=desc,
        description_canonical=desc,
        unidad=unit,  # type: ignore[arg-type]
        cantidad=Decimal(qty),
        confidence=1.0,
        source_page=3,
        requires_review=True,
    )


def _make_guia(guia_id: str = "g1", registro: str = "R001") -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=date(2026, 5, 28),
        lines=[_make_line()],
        source_pages=[0],
    )


def _make_registro(numero: str = "R001") -> Registro:
    return Registro(
        numero=numero,
        fecha_declarada=date(2026, 5, 28),
        declared_lines=[_make_line()],
    )


def _make_ctx(tmp_path: Path, run_id: str = "test-recover") -> RunContext:
    ctx = RunContext(
        pdf_path=tmp_path / "input.pdf",
        output_base=tmp_path / "runs",
        run_id=run_id,
    )
    ctx.write_review_sidecar({"edits": [], "audit_trail": []})
    return ctx


def _make_errored(guia_id: str = "errored-g1", registro: str = "R001") -> ErroredGuia:
    return ErroredGuia(registro=registro, guia_id=guia_id, source_pages=[3])


def _make_recovered_guia(
    guia_id: str = "errored-g1",
    registro: str = "R001",
) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=date(2026, 5, 28),
        fecha_entrega=date(2026, 5, 28),
        lines=[_make_line()],
        source_pages=[3],
        identity_source="qr",
    )


# ---------------------------------------------------------------------------
# T-4 tests
# ---------------------------------------------------------------------------


class TestRecoveredGuiaSidecarReplay:
    """Sidecar emits recovered_guia event; replay on restart moves guía out of errored."""

    def test_recovered_guia_event_emitted_to_sidecar(self, tmp_path: Path) -> None:
        """add_recovered_guia must write a recovered_guia event to the sidecar."""
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)
        errored = [_make_errored()]

        service = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )
        recovered = _make_recovered_guia()
        service.add_recovered_guia(recovered)

        sidecar = ctx.read_review_sidecar()
        edits = sidecar.get("edits", [])
        recovered_events = [e for e in edits if e.get("kind") == "recovered_guia"]
        assert len(recovered_events) == 1
        assert recovered_events[0]["target"]["guia_id"] == "errored-g1"

    def test_restart_replay_removes_from_errored(self, tmp_path: Path) -> None:
        """On restart, a recovered_guia event removes the guía from errored_guias."""
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        errored = [_make_errored("errored-g1")]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        # Session 1: recover the guía.
        svc1 = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )
        svc1.add_recovered_guia(_make_recovered_guia("errored-g1"))

        # Session 2: restore_from_sidecar should replay the recovery.
        svc2 = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=list(errored),  # fresh copy (as if loaded from cache)
        )

        remaining_errored_ids = {e.guia_id for e in svc2.errored_guias}
        assert "errored-g1" not in remaining_errored_ids

    def test_restart_replay_adds_guia_to_guias_list(self, tmp_path: Path) -> None:
        """On restart, the replayed recovered guía must appear in svc2.guias."""
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        errored = [_make_errored("errored-g1")]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        svc1 = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )
        svc1.add_recovered_guia(_make_recovered_guia("errored-g1"))

        svc2 = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=list(errored),
        )

        guia_ids = {g.guia_id for g in svc2.guias}
        assert "errored-g1" in guia_ids

    def test_restart_no_refetch_required(self, tmp_path: Path) -> None:
        """Replay must reconstruct the guía from sidecar data — NO external call needed.

        The sidecar stores the fully-normalized GuiaDeRemision JSON so the replay
        never re-fetches from SUNAT.
        """
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        errored = [_make_errored("errored-g1")]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        svc1 = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )
        recovered = _make_recovered_guia("errored-g1")
        svc1.add_recovered_guia(recovered)

        # Check that sidecar new_value contains the full guía JSON.
        sidecar = ctx.read_review_sidecar()
        edits = sidecar.get("edits", [])
        ev = next(e for e in edits if e.get("kind") == "recovered_guia")
        # new_value must be a dict (serialized GuiaDeRemision) — never a string
        assert isinstance(ev["new_value"], dict), (
            "recovered_guia.new_value must be a dict (model_dump) for restart replay"
        )
        assert "guia_id" in ev["new_value"]

    def test_idempotent_replay_no_duplicate(self, tmp_path: Path) -> None:
        """If the sidecar has one recovered_guia event, restart replay must add it exactly once."""
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        errored = [_make_errored("errored-g1")]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        svc1 = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )
        svc1.add_recovered_guia(_make_recovered_guia("errored-g1"))

        svc2 = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=list(errored),
        )

        count = sum(1 for g in svc2.guias if g.guia_id == "errored-g1")
        assert count == 1, f"Expected exactly 1 recovered guía in replay; found {count}"
