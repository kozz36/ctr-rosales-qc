"""F4 operator-assigned canonical correction (REV-R25 / D9).

When apply_guia_line_edit is called with assign_material_canonical set,
the line must be reassigned to the operator-chosen declared canonical with:
  - description_canonical = assign_material_canonical
  - match_method = "operator"
  - requires_review = True

Strict-TDD: failing tests written FIRST (RED). These MUST fail before
the service extension is implemented.
"""

from __future__ import annotations

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


def _make_declared_line(
    desc: str = "BARRA A615 G60 1/2 9M",
    qty: str = "4.124",
    unit: str = "TN",
) -> MaterialLine:
    return MaterialLine(
        description_raw=desc,
        description_canonical=desc,
        unidad=unit,  # type: ignore[arg-type]
        cantidad=Decimal(qty),
        confidence=1.0,
    )


def _make_guia_line(
    desc: str = "acero dimensionado",
    qty: str = "2.0",
    unit: str = "TN",
    requires_review: bool = False,
) -> MaterialLine:
    return MaterialLine(
        description_raw=desc,
        description_canonical=desc,
        unidad=unit,  # type: ignore[arg-type]
        cantidad=Decimal(qty),
        confidence=0.5,
        source_page=5,
        requires_review=requires_review,
    )


def _make_guia(
    guia_id: str = "T009-0001",
    registro: str = "R001",
    lines: list[MaterialLine] | None = None,
) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=date(2026, 5, 28),
        lines=lines or [_make_guia_line()],
        source_pages=[5],
    )


def _make_registro(
    numero: str = "R001",
    lines: list[MaterialLine] | None = None,
) -> Registro:
    return Registro(
        numero=numero,
        fecha_declarada=date(2026, 5, 28),
        declared_lines=lines or [_make_declared_line()],
    )


def _make_ctx(tmp_path: Path, run_id: str = "test-operator-assign") -> RunContext:
    ctx = RunContext(
        pdf_path=tmp_path / "input.pdf",
        output_base=tmp_path / "runs",
        run_id=run_id,
    )
    ctx.write_review_sidecar({"edits": [], "audit_trail": []})
    return ctx


# ---------------------------------------------------------------------------
# Task 2.1 — apply_guia_line_edit with assign_material_canonical sets fields
# ---------------------------------------------------------------------------


class TestOperatorAssignedCanonical:
    """apply_guia_line_edit with assign_material_canonical (task 2.1 RED)."""

    def test_assign_material_canonical_sets_description_canonical(
        self, tmp_path: Path
    ) -> None:
        """Task 2.1 RED: assign_material_canonical changes the line's description_canonical."""
        ctx = _make_ctx(tmp_path)
        guia = _make_guia(lines=[_make_guia_line(desc="acero dimensionado")])
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, [guia])

        svc = ReviewService(declared=declared, guias=[guia], rows=rows, ctx=ctx)

        svc.apply_guia_line_edit(
            guia_id="T009-0001",
            line_index=0,
            material_canonical="acero dimensionado",  # existing canonical for lookup
            new_cantidad=Decimal("2.0"),
            assign_material_canonical="BARRA A615 G60 1/2 9M",
        )

        # Verify the line was updated with the operator-chosen canonical
        updated_guia = next(g for g in svc.guias if g.guia_id == "T009-0001")
        line = updated_guia.lines[0]
        assert line.description_canonical == "BARRA A615 G60 1/2 9M"

    def test_assign_material_canonical_sets_match_method_operator(
        self, tmp_path: Path
    ) -> None:
        """Task 2.1 RED: match_method must be 'operator' after canonical assignment."""
        ctx = _make_ctx(tmp_path)
        guia = _make_guia(lines=[_make_guia_line(desc="acero dimensionado")])
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, [guia])

        svc = ReviewService(declared=declared, guias=[guia], rows=rows, ctx=ctx)

        svc.apply_guia_line_edit(
            guia_id="T009-0001",
            line_index=0,
            material_canonical="acero dimensionado",
            new_cantidad=Decimal("2.0"),
            assign_material_canonical="BARRA A615 G60 1/2 9M",
        )

        updated_guia = next(g for g in svc.guias if g.guia_id == "T009-0001")
        line = updated_guia.lines[0]
        assert line.match_method == "operator"

    def test_assign_material_canonical_sets_requires_review_true(
        self, tmp_path: Path
    ) -> None:
        """Task 2.1 RED: requires_review must be True after canonical assignment."""
        ctx = _make_ctx(tmp_path)
        guia = _make_guia(lines=[_make_guia_line(desc="acero dimensionado", requires_review=False)])
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, [guia])

        svc = ReviewService(declared=declared, guias=[guia], rows=rows, ctx=ctx)

        svc.apply_guia_line_edit(
            guia_id="T009-0001",
            line_index=0,
            material_canonical="acero dimensionado",
            new_cantidad=Decimal("2.0"),
            assign_material_canonical="BARRA A615 G60 1/2 9M",
        )

        updated_guia = next(g for g in svc.guias if g.guia_id == "T009-0001")
        line = updated_guia.lines[0]
        assert line.requires_review is True

    def test_assign_material_canonical_emits_manual_correction_audit_event(
        self, tmp_path: Path
    ) -> None:
        """Task 2.1 RED: audit trail must include a manual_correction event."""
        ctx = _make_ctx(tmp_path)
        guia = _make_guia(lines=[_make_guia_line()])
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, [guia])

        svc = ReviewService(declared=declared, guias=[guia], rows=rows, ctx=ctx)

        svc.apply_guia_line_edit(
            guia_id="T009-0001",
            line_index=0,
            material_canonical="acero dimensionado",
            new_cantidad=Decimal("2.0"),
            assign_material_canonical="BARRA A615 G60 1/2 9M",
        )

        trail = svc.get_audit_trail()
        kinds = [e["kind"] for e in trail]
        assert "manual_correction" in kinds

    def test_assign_none_preserves_cantidad_only_path(self, tmp_path: Path) -> None:
        """Task 2.1 backward-compat: assign_material_canonical=None → original path unchanged."""
        ctx = _make_ctx(tmp_path)
        guia = _make_guia(lines=[_make_guia_line(desc="acero dimensionado")])
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, [guia])

        svc = ReviewService(declared=declared, guias=[guia], rows=rows, ctx=ctx)

        svc.apply_guia_line_edit(
            guia_id="T009-0001",
            line_index=0,
            material_canonical="acero dimensionado",
            new_cantidad=Decimal("3.0"),
            # No assign_material_canonical — original path
        )

        updated_guia = next(g for g in svc.guias if g.guia_id == "T009-0001")
        line = updated_guia.lines[0]
        # canonical stays the same; only cantidad changes
        assert line.description_canonical == "acero dimensionado"
        assert line.match_method == "deterministic"  # default
        assert line.cantidad == Decimal("3.0")


# ---------------------------------------------------------------------------
# Task 2.2 — DTO regression: match_method="operator" must serialize without 500
# ---------------------------------------------------------------------------


class TestOperatorMatchMethodDTO:
    """Regression test: match_method='operator' must not 500 the table endpoint.

    This is the exact class of bug that returned HTTP 500 on grade_tolerant —
    the DTO Literal was too narrow and Pydantic v2 raised a validation error.
    Task 2.2 RED.
    """

    def test_reprocess_batch_response_has_operator_in_match_method_literal(self) -> None:
        """Task 2.2 RED: GuiaLineEditRequest with assign_material_canonical passes DTO validation.

        This also implicitly tests that the schemas module accepts the field.
        """
        from reconciliation.infrastructure.api.schemas import (  # noqa: PLC0415
            GuiaLineEditRequest,
        )

        # Must not raise pydantic ValidationError
        req = GuiaLineEditRequest(
            line_index=0,
            material_canonical="acero dimensionado",
            cantidad=2.0,
            assign_material_canonical="BARRA A615 G60 1/2 9M",
        )
        assert req.assign_material_canonical == "BARRA A615 G60 1/2 9M"

    def test_reconciliation_row_response_serializes_operator_match_method(self) -> None:
        """ReconciliationRowResponse must serialize match_method='operator' without raising.

        Task 2.2 RED: MatchMethod in material_key.py must include 'operator';
        if the DTO Literal is too narrow, model_validate will raise a ValidationError.
        """
        from reconciliation.infrastructure.api.schemas import (  # noqa: PLC0415
            ReconciliationRowResponse,
        )

        row = ReconciliationRowResponse(
            row_id="R001|None|BARRA A615 G60 1/2 9M|TN",
            registro="R001",
            fecha=None,
            material_canonical="BARRA A615 G60 1/2 9M",
            unidad="TN",
            declared_qty=Decimal("4.124"),
            summed_qty=Decimal("4.124"),
            delta=Decimal("0"),
            status="MATCH",
            source_pages=[5],
            match_method="operator",  # type: ignore[arg-type]  # must be accepted after fix
        )
        serialized = row.model_dump()
        assert serialized["match_method"] == "operator"


# ---------------------------------------------------------------------------
# F4 sidecar round-trip: manual_correction replay after restart (RED first)
# ---------------------------------------------------------------------------


class TestManualCorrectionSidecarRoundTrip:
    """Strict-TDD RED: manual_correction must survive a server restart.

    Failure class: operator-assign audit event is emitted + persisted but
    restore_from_sidecar has NO replay branch for 'manual_correction'.
    On restart the line reverts to its pre-correction description_canonical
    and match_method — silent data loss.

    The persisted event must carry BOTH the operator-chosen canonical AND the
    corrected cantidad so the replay produces the exact post-correction state.

    These tests MUST FAIL against current code (no replay branch exists).
    """

    def _setup_and_apply(
        self, tmp_path: Path
    ) -> tuple["ReviewService", list, list, "RunContext"]:
        """Shared setup: create svc, apply operator correction, return originals for restore."""
        ctx = _make_ctx(tmp_path)
        guia = _make_guia(lines=[_make_guia_line(desc="acero dimensionado", qty="2.0")])
        declared = [_make_registro()]
        rows = ReconciliationService().reconcile(declared, [guia])

        svc = ReviewService(declared=declared, guias=[guia], rows=rows, ctx=ctx)
        svc.apply_guia_line_edit(
            guia_id="T009-0001",
            line_index=0,
            material_canonical="acero dimensionado",
            new_cantidad=Decimal("3.5"),
            assign_material_canonical="BARRA A615 G60 1/2 9M",
        )
        # Return originals so restore_from_sidecar starts fresh
        fresh_guias = [_make_guia(lines=[_make_guia_line(desc="acero dimensionado", qty="2.0")])]
        fresh_rows = ReconciliationService().reconcile(declared, fresh_guias)
        return svc, declared, fresh_guias, fresh_rows, ctx

    def test_sidecar_round_trip_restores_canonical(self, tmp_path: Path) -> None:
        """manual_correction survives persist → fresh restore: description_canonical."""
        _, declared, fresh_guias, fresh_rows, ctx = self._setup_and_apply(tmp_path)

        # Simulate server restart: restore from sidecar into a fresh service.
        restored = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=fresh_guias,
            rows=fresh_rows,
            ctx=ctx,
            errored_guias=[],
        )

        restored_guia = next(g for g in restored.guias if g.guia_id == "T009-0001")
        line = restored_guia.lines[0]
        assert line.description_canonical == "BARRA A615 G60 1/2 9M", (
            f"Expected operator canonical after replay, got {line.description_canonical!r}"
        )

    def test_sidecar_round_trip_restores_match_method(self, tmp_path: Path) -> None:
        """manual_correction survives persist → fresh restore: match_method='operator'."""
        _, declared, fresh_guias, fresh_rows, ctx = self._setup_and_apply(tmp_path)

        restored = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=fresh_guias,
            rows=fresh_rows,
            ctx=ctx,
            errored_guias=[],
        )

        restored_guia = next(g for g in restored.guias if g.guia_id == "T009-0001")
        line = restored_guia.lines[0]
        assert line.match_method == "operator", (
            f"Expected match_method='operator' after replay, got {line.match_method!r}"
        )

    def test_sidecar_round_trip_preserves_requires_review(self, tmp_path: Path) -> None:
        """manual_correction replay keeps requires_review=True."""
        _, declared, fresh_guias, fresh_rows, ctx = self._setup_and_apply(tmp_path)

        restored = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=fresh_guias,
            rows=fresh_rows,
            ctx=ctx,
            errored_guias=[],
        )

        restored_guia = next(g for g in restored.guias if g.guia_id == "T009-0001")
        line = restored_guia.lines[0]
        assert line.requires_review is True, "Replay must keep requires_review=True"

    def test_sidecar_round_trip_restores_cantidad(self, tmp_path: Path) -> None:
        """manual_correction replay restores the corrected cantidad (faithful replay).

        The persisted event must store cantidad alongside canonical so the replay
        reproduces the EXACT post-correction state — not just the canonical.
        """
        _, declared, fresh_guias, fresh_rows, ctx = self._setup_and_apply(tmp_path)

        restored = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=fresh_guias,
            rows=fresh_rows,
            ctx=ctx,
            errored_guias=[],
        )

        restored_guia = next(g for g in restored.guias if g.guia_id == "T009-0001")
        line = restored_guia.lines[0]
        assert line.cantidad == Decimal("3.5"), (
            f"Expected corrected cantidad=3.5 after replay, got {line.cantidad}"
        )
