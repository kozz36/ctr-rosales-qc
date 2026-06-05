"""FIX 1: ReviewService.mark_retry_attempted wires the failure-path UX flag.

When apply_retry fails (recovered=False: no_hashqr_url / sunat_none / sunat_empty),
the matching ErroredGuia must be DURABLY marked retry_attempted=True so the
frontend gates the REINTENTAR button + "SUNAT no disponible" hint.

Strict-TDD: failing tests written BEFORE implementation. Real preconditions:
errored guia present, retry FAILED (guia stays errored, flag flips to True).
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


def _make_ctx(tmp_path: Path, run_id: str = "test-retry-flag") -> RunContext:
    ctx = RunContext(
        pdf_path=tmp_path / "input.pdf",
        output_base=tmp_path / "runs",
        run_id=run_id,
    )
    ctx.write_review_sidecar({"edits": [], "audit_trail": []})
    return ctx


def _make_errored(guia_id: str = "errored-g1", registro: str = "R001") -> ErroredGuia:
    return ErroredGuia(registro=registro, guia_id=guia_id, source_pages=[3])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMarkRetryAttempted:
    """mark_retry_attempted flips the flag, persists, and replays on restart."""

    def test_flag_starts_false(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, guias)
        errored = [_make_errored()]
        svc = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )
        assert svc.errored_guias[0].retry_attempted is False

    def test_mark_sets_flag_on_matching_entry(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, guias)
        errored = [_make_errored("errored-g1"), _make_errored("errored-g2")]
        svc = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )

        svc.mark_retry_attempted("errored-g1")

        by_id = {e.guia_id: e for e in svc.errored_guias}
        assert by_id["errored-g1"].retry_attempted is True
        # Other entries untouched.
        assert by_id["errored-g2"].retry_attempted is False

    def test_mark_emits_sidecar_event(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, guias)
        errored = [_make_errored("errored-g1")]
        svc = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )

        svc.mark_retry_attempted("errored-g1")

        sidecar = ctx.read_review_sidecar()
        events = [
            e for e in sidecar.get("edits", []) if e.get("kind") == "retry_attempted"
        ]
        assert len(events) == 1
        assert events[0]["target"]["guia_id"] == "errored-g1"

    def test_mark_unknown_guia_id_is_noop(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, guias)
        errored = [_make_errored("errored-g1")]
        svc = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )

        # Should not raise; no entry changes.
        svc.mark_retry_attempted("does-not-exist")
        assert svc.errored_guias[0].retry_attempted is False

    def test_restore_from_sidecar_replays_flag(self, tmp_path: Path) -> None:
        """The retry_attempted flag survives restart via sidecar replay."""
        ctx = _make_ctx(tmp_path)
        guias = [_make_guia()]
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, guias)
        errored = [_make_errored("errored-g1")]

        svc1 = ReviewService(
            declared=declared, guias=guias, rows=rows, ctx=ctx, errored_guias=errored
        )
        svc1.mark_retry_attempted("errored-g1")

        # Restart: errored hydrated fresh from cache (flag back to False),
        # replay must re-apply it.
        svc2 = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=[_make_errored("errored-g1")],
        )

        assert svc2.errored_guias[0].retry_attempted is True
