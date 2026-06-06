"""Tests for ReviewService — edit/reassign/persist/reload.

Covers:
  - apply_edit (fecha, registro fields)
  - apply_reassignment (delegates to ReconciliationService, re-reconciles)
  - audit trail accumulation
  - sidecar round-trip (write → read → verify)
  - restore_from_sidecar (restart/resumability)
  - errored_guias constructor state + read-only property (REV-E03)
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from reconciliation.application.review_service import ReviewService, _parse_date
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
    confidence: float | None = 0.95,
    page: int = 0,
    requires_review: bool = False,
) -> MaterialLine:
    return MaterialLine(
        description_raw=desc,
        description_canonical=desc,
        unidad=unit,  # type: ignore[arg-type]
        cantidad=Decimal(qty),
        confidence=confidence,
        source_page=page,
        requires_review=requires_review,
    )


def _make_guia(
    guia_id: str = "g1",
    registro: str | None = "R001",
    fecha: date | None = date(2024, 3, 10),
    lines: list[MaterialLine] | None = None,
) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=fecha,
        fecha_confidence=0.95,
        lines=lines or [_make_line()],
        source_pages=[0],
    )


def _make_registro(
    numero: str = "R001",
    fecha: date | None = date(2024, 3, 10),
    lines: list[MaterialLine] | None = None,
) -> Registro:
    return Registro(
        numero=numero,
        fecha_declarada=fecha,
        declared_lines=lines or [_make_line()],
    )


def _build_service(
    tmp_path: Path,
    guias: list[GuiaDeRemision] | None = None,
    declared: list[Registro] | None = None,
    rows: list[ReconciliationRow] | None = None,
    run_id: str = "test-run",
) -> tuple[ReviewService, RunContext]:
    ctx = RunContext(
        pdf_path=tmp_path / "input.pdf",
        output_base=tmp_path / "runs",
        run_id=run_id,
    )
    # Initialise sidecar
    ctx.write_review_sidecar({"edits": [], "audit_trail": []})

    g = guias or [_make_guia()]
    d = declared or [_make_registro()]
    reconciler = ReconciliationService()
    r = rows or reconciler.reconcile(d, g)
    service = ReviewService(declared=d, guias=g, rows=r, ctx=ctx)
    return service, ctx


# ---------------------------------------------------------------------------
# apply_edit — fecha
# ---------------------------------------------------------------------------


class TestApplyEditFecha:
    def test_edit_fecha_updates_guia(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        new_date = date(2024, 6, 1)
        service.apply_edit("g1", "fecha", new_date)
        updated_guia = next(g for g in service.guias if g.guia_id == "g1")
        assert updated_guia.fecha == new_date

    def test_edit_fecha_from_iso_string(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_edit("g1", "fecha", "2025-12-31")
        updated_guia = next(g for g in service.guias if g.guia_id == "g1")
        assert updated_guia.fecha == date(2025, 12, 31)

    def test_edit_fecha_to_none(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_edit("g1", "fecha", None)
        updated_guia = next(g for g in service.guias if g.guia_id == "g1")
        assert updated_guia.fecha is None

    def test_edit_fecha_triggers_recompute(self, tmp_path: Path) -> None:
        """Rows are recomputed after edit (new object identity)."""
        service, _ = _build_service(tmp_path)
        rows_before = service.rows
        new_rows = service.apply_edit("g1", "fecha", date(2025, 1, 1))
        # Rows should be new objects (fresh reconcile output)
        assert new_rows is not rows_before

    def test_edit_missing_guia_raises(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            service.apply_edit("nonexistent-guia", "fecha", date(2024, 1, 1))

    def test_edit_unsupported_field_raises(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        with pytest.raises(ValueError, match="Unsupported field"):
            service.apply_edit("g1", "guia_id", "new-id")


# ---------------------------------------------------------------------------
# apply_edit — registro
# ---------------------------------------------------------------------------


class TestApplyEditRegistro:
    def test_edit_registro_updates_guia(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_edit("g1", "registro", "R999")
        updated = next(g for g in service.guias if g.guia_id == "g1")
        assert updated.registro == "R999"

    def test_edit_registro_to_none(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_edit("g1", "registro", None)
        updated = next(g for g in service.guias if g.guia_id == "g1")
        assert updated.registro is None

    def test_edit_registro_non_string_raises(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        with pytest.raises(ValueError):
            service.apply_edit("g1", "registro", 12345)


# ---------------------------------------------------------------------------
# apply_reassignment
# ---------------------------------------------------------------------------


class TestApplyReassignment:
    def test_reassignment_changes_registro(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_reassignment("g1", new_registro="R002", new_fecha=date(2024, 4, 1))
        updated = next(g for g in service.guias if g.guia_id == "g1")
        assert updated.registro == "R002"
        assert updated.fecha == date(2024, 4, 1)

    def test_reassignment_fecha_from_string(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_reassignment("g1", new_registro="R002", new_fecha="2024-04-01")
        updated = next(g for g in service.guias if g.guia_id == "g1")
        assert updated.fecha == date(2024, 4, 1)

    def test_reassignment_recomputes_rows(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        rows_before = service.rows
        new_rows = service.apply_reassignment(
            "g1", new_registro="R999", new_fecha=date(2024, 4, 1)
        )
        assert new_rows is not rows_before

    def test_reassignment_missing_guia_raises(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            service.apply_reassignment("ghost", new_registro="R001", new_fecha=date(2024, 1, 1))

    def test_reassignment_pure_no_mutation_on_error(self, tmp_path: Path) -> None:
        """Guías list must not be mutated when an error occurs."""
        service, _ = _build_service(tmp_path)
        guias_before = list(service.guias)
        with pytest.raises(ValueError):
            service.apply_reassignment("ghost", "R001", date(2024, 1, 1))
        assert service.guias == guias_before


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_empty_trail_initially(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        assert service.get_audit_trail() == []

    def test_edit_appended_to_trail(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_edit("g1", "fecha", date(2024, 1, 1))
        trail = service.get_audit_trail()
        assert len(trail) == 1
        assert trail[0]["kind"] == "field_edit"
        assert trail[0]["field"] == "fecha"

    def test_reassignment_appended_to_trail(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_reassignment("g1", "R002", date(2024, 2, 1))
        trail = service.get_audit_trail()
        assert len(trail) == 1
        assert trail[0]["kind"] == "reassignment"

    def test_multiple_edits_ordered(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_edit("g1", "fecha", date(2024, 1, 1))
        service.apply_edit("g1", "registro", "R002")
        trail = service.get_audit_trail()
        assert len(trail) == 2
        assert trail[0]["field"] == "fecha"
        assert trail[1]["field"] == "registro"

    def test_trail_event_has_timestamp(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        service.apply_edit("g1", "fecha", date(2024, 1, 1))
        event = service.get_audit_trail()[0]
        assert "timestamp" in event
        assert event["timestamp"]  # non-empty


# ---------------------------------------------------------------------------
# Sidecar round-trip
# ---------------------------------------------------------------------------


class TestSidecarRoundTrip:
    def test_sidecar_written_after_edit(self, tmp_path: Path) -> None:
        service, ctx = _build_service(tmp_path)
        service.apply_edit("g1", "fecha", date(2024, 1, 1))
        sidecar = ctx.read_review_sidecar()
        assert len(sidecar["edits"]) == 1

    def test_sidecar_written_after_reassignment(self, tmp_path: Path) -> None:
        service, ctx = _build_service(tmp_path)
        service.apply_reassignment("g1", "R002", date(2024, 2, 1))
        sidecar = ctx.read_review_sidecar()
        assert len(sidecar["edits"]) == 1

    def test_sidecar_is_valid_json(self, tmp_path: Path) -> None:
        service, ctx = _build_service(tmp_path)
        service.apply_edit("g1", "fecha", date(2024, 6, 15))
        raw = ctx.review_sidecar.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert "edits" in parsed

    def test_sidecar_accumulates_multiple_edits(self, tmp_path: Path) -> None:
        service, ctx = _build_service(tmp_path)
        service.apply_edit("g1", "fecha", date(2024, 1, 1))
        service.apply_edit("g1", "fecha", date(2024, 2, 1))
        sidecar = ctx.read_review_sidecar()
        assert len(sidecar["edits"]) == 2


# ---------------------------------------------------------------------------
# restore_from_sidecar (restart/resumability)
# ---------------------------------------------------------------------------


class TestRestoreFromSidecar:
    def test_restore_replays_field_edits(self, tmp_path: Path) -> None:
        """After a simulated restart, applied edits are replayed from sidecar."""
        # First session: apply an edit and save
        service1, ctx = _build_service(tmp_path, run_id="restart-test")
        service1.apply_edit("g1", "fecha", date(2025, 7, 4))

        # Second session: rebuild fresh objects and restore
        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        service2 = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
        )
        restored_guia = next(g for g in service2.guias if g.guia_id == "g1")
        assert restored_guia.fecha == date(2025, 7, 4)

    def test_restore_replays_reassignment(self, tmp_path: Path) -> None:
        service1, ctx = _build_service(tmp_path, run_id="reassign-test")
        service1.apply_reassignment("g1", "R999", date(2025, 8, 1))

        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        service2 = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
        )
        restored_guia = next(g for g in service2.guias if g.guia_id == "g1")
        assert restored_guia.registro == "R999"
        assert restored_guia.fecha == date(2025, 8, 1)

    def test_restore_empty_sidecar_returns_fresh_service(self, tmp_path: Path) -> None:
        """Empty/missing sidecar yields a service with no edits applied."""
        ctx = RunContext(
            pdf_path=tmp_path / "in.pdf",
            output_base=tmp_path / "runs",
            run_id="empty-test",
        )
        # Sidecar not written at all
        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        service = ReviewService.restore_from_sidecar(
            declared=declared, guias=guias, rows=rows, ctx=ctx
        )
        assert service.get_audit_trail() == []

    def test_restore_tolerates_unknown_guia_in_sidecar(self, tmp_path: Path) -> None:
        """Sidecar with an unknown guia_id must not crash the restore."""
        ctx = RunContext(
            pdf_path=tmp_path / "in.pdf",
            output_base=tmp_path / "runs",
            run_id="unknown-guia-test",
        )
        ctx.write_review_sidecar({
            "edits": [{
                "kind": "field_edit",
                "target": {"guia_id": "ghost-guia"},
                "field": "fecha",
                "new_value": "2024-01-01",
            }],
            "audit_trail": [],
        })
        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        # Should not raise
        service = ReviewService.restore_from_sidecar(
            declared=declared, guias=guias, rows=rows, ctx=ctx
        )
        # Original guia untouched
        assert service.guias[0].guia_id == "g1"

    def test_restore_multiple_edits_in_order(self, tmp_path: Path) -> None:
        """Multiple sidecar edits are replayed in insertion order."""
        service1, ctx = _build_service(tmp_path, run_id="multi-edit-test")
        service1.apply_edit("g1", "fecha", date(2024, 1, 1))
        service1.apply_edit("g1", "fecha", date(2024, 6, 30))  # later wins

        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        service2 = ReviewService.restore_from_sidecar(
            declared=declared, guias=guias, rows=rows, ctx=ctx
        )
        restored_guia = next(g for g in service2.guias if g.guia_id == "g1")
        # The last edit wins
        assert restored_guia.fecha == date(2024, 6, 30)

    def test_restore_replays_guia_line_edit(self, tmp_path: Path) -> None:
        """B2: a persisted guia_line_edit must SURVIVE a restart (not revert)."""
        from decimal import Decimal  # noqa: PLC0415

        line = _make_line(qty="100", desc="barra 3/8")
        guia = _make_guia(lines=[line])
        declared = [_make_registro(lines=[_make_line(qty="200", desc="barra 3/8")])]
        service1, ctx = _build_service(
            tmp_path, guias=[guia], declared=declared, run_id="line-edit-restart"
        )
        service1.apply_guia_line_edit(
            guia_id="g1",
            line_index=None,
            material_canonical="barra 3/8",
            new_cantidad=Decimal("200"),
        )

        # Fresh restart: rebuild original objects (cantidad still 100)
        fresh_guia = _make_guia(lines=[_make_line(qty="100", desc="barra 3/8")])
        fresh_declared = [_make_registro(lines=[_make_line(qty="200", desc="barra 3/8")])]
        reconciler = ReconciliationService()
        fresh_rows = reconciler.reconcile(fresh_declared, [fresh_guia])

        service2 = ReviewService.restore_from_sidecar(
            declared=fresh_declared, guias=[fresh_guia], rows=fresh_rows, ctx=ctx
        )
        restored = next(g for g in service2.guias if g.guia_id == "g1")
        # Edit must have been replayed: cantidad stays 200, NOT reverted to 100.
        assert restored.lines[0].cantidad == Decimal("200")


# ---------------------------------------------------------------------------
# apply_guia_line_edit (S1.7 — rev-2 line-level edit)
# ---------------------------------------------------------------------------


class TestApplyGuiaLineEdit:
    def test_updates_line_cantidad_by_index(self, tmp_path: Path) -> None:
        """Update line at index 0 → cantidad changes; summed_qty recomputed."""
        line = _make_line(qty="100", desc="barra 3/8")
        guia = _make_guia(lines=[line])
        declared = [_make_registro(lines=[_make_line(qty="200", desc="barra 3/8")])]
        service, _ = _build_service(tmp_path, guias=[guia], declared=declared)

        from decimal import Decimal  # noqa: PLC0415
        service.apply_guia_line_edit(
            guia_id="g1",
            line_index=0,
            material_canonical=None,
            new_cantidad=Decimal("200"),
        )
        updated_guia = next(g for g in service.guias if g.guia_id == "g1")
        assert updated_guia.lines[0].cantidad == Decimal("200")

    def test_recomputes_match_after_edit(self, tmp_path: Path) -> None:
        """After edit that makes guia qty equal declared → MATCH row."""
        line = _make_line(qty="80", desc="barra 3/8")
        guia = _make_guia(lines=[line])
        declared = [_make_registro(lines=[_make_line(qty="100", desc="barra 3/8")])]
        service, _ = _build_service(tmp_path, guias=[guia], declared=declared)

        from decimal import Decimal  # noqa: PLC0415
        rows = service.apply_guia_line_edit(
            guia_id="g1",
            line_index=0,
            material_canonical=None,
            new_cantidad=Decimal("100"),
        )
        # Find the row for this guia
        match_rows = [r for r in rows if r.status == "MATCH"]
        assert len(match_rows) >= 1

    def test_updates_line_by_material_canonical(self, tmp_path: Path) -> None:
        """Lookup by material_canonical when line_index is None."""
        line = _make_line(qty="50", desc="alambre n16")
        guia = _make_guia(lines=[line])
        declared = [_make_registro(lines=[_make_line(qty="50", desc="alambre n16")])]
        service, _ = _build_service(tmp_path, guias=[guia], declared=declared)

        from decimal import Decimal  # noqa: PLC0415
        service.apply_guia_line_edit(
            guia_id="g1",
            line_index=None,
            material_canonical="alambre n16",
            new_cantidad=Decimal("75"),
        )
        updated = next(g for g in service.guias if g.guia_id == "g1")
        assert updated.lines[0].cantidad == Decimal("75")

    def test_negative_cantidad_raises(self, tmp_path: Path) -> None:
        """cantidad < 0 must raise ValueError."""
        service, _ = _build_service(tmp_path)
        from decimal import Decimal  # noqa: PLC0415
        with pytest.raises(ValueError, match="must be >= 0"):
            service.apply_guia_line_edit("g1", 0, None, Decimal("-1"))

    def test_unknown_guia_id_raises(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        from decimal import Decimal  # noqa: PLC0415
        with pytest.raises(ValueError, match="not found"):
            service.apply_guia_line_edit("ghost", 0, None, Decimal("10"))

    def test_out_of_range_line_index_raises(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        from decimal import Decimal  # noqa: PLC0415
        with pytest.raises(ValueError, match="out of range"):
            service.apply_guia_line_edit("g1", 99, None, Decimal("10"))

    def test_audit_trail_records_guia_line_edit(self, tmp_path: Path) -> None:
        service, _ = _build_service(tmp_path)
        from decimal import Decimal  # noqa: PLC0415
        service.apply_guia_line_edit("g1", 0, None, Decimal("50"))
        trail = service.get_audit_trail()
        assert len(trail) == 1
        assert trail[0]["kind"] == "guia_line_edit"
        assert trail[0]["field"] == "cantidad"

    def test_apply_edit_summed_qty_field_raises(self, tmp_path: Path) -> None:
        """apply_edit must explicitly reject 'summed_qty' (REC-C04)."""
        service, _ = _build_service(tmp_path)
        with pytest.raises(ValueError, match="computed property"):
            service.apply_edit("g1", "summed_qty", "999")


# ---------------------------------------------------------------------------
# B3: vision_audit provenance survives the first review mutation
# ---------------------------------------------------------------------------


class TestVisionAuditPreservation:
    def test_persist_preserves_vision_audit_on_edit(self, tmp_path: Path) -> None:
        """B3: the first apply_edit must NOT destroy the pipeline's vision_audit."""
        service, ctx = _build_service(tmp_path, run_id="vision-audit-test")
        # Pipeline wrote a vision_audit record before any review mutation.
        ctx.append_vision_audit(
            {"stage": "vision", "calls_made": 3, "cap_reached": False}
        )

        service.apply_edit("g1", "fecha", date(2025, 1, 1))

        sidecar = ctx.read_review_sidecar()
        assert "vision_audit" in sidecar
        assert sidecar["vision_audit"] == [
            {"stage": "vision", "calls_made": 3, "cap_reached": False}
        ]
        # Review state is still persisted alongside it.
        assert len(sidecar["edits"]) == 1

    def test_persist_preserves_vision_audit_on_line_edit(self, tmp_path: Path) -> None:
        """B3: apply_guia_line_edit must also preserve vision_audit."""
        from decimal import Decimal  # noqa: PLC0415

        service, ctx = _build_service(tmp_path, run_id="vision-audit-line-test")
        ctx.append_vision_audit({"stage": "vision", "calls_made": 1})
        service.apply_guia_line_edit("g1", 0, None, Decimal("42"))
        sidecar = ctx.read_review_sidecar()
        assert sidecar.get("vision_audit") == [{"stage": "vision", "calls_made": 1}]


# ---------------------------------------------------------------------------
# B4: reject a section-ID (Contents-ID) as a Registro N°
# ---------------------------------------------------------------------------


class TestSectionIdGuard:
    def test_reassign_to_section_id_rejected(self, tmp_path: Path) -> None:
        """B4: reassigning a guía to a section-ID (e.g. 4252) must raise ValueError."""
        service, _ = _build_service(tmp_path)
        with pytest.raises(ValueError, match="section"):
            service.apply_reassignment("g1", new_registro="4252", new_fecha=date(2024, 4, 1))

    def test_reassign_to_valid_registro_ok(self, tmp_path: Path) -> None:
        """B4: a realistic registro (232) is still accepted."""
        service, _ = _build_service(tmp_path)
        service.apply_reassignment("g1", new_registro="232", new_fecha=date(2024, 4, 1))
        restored = next(g for g in service.guias if g.guia_id == "g1")
        assert restored.registro == "232"

    def test_edit_registro_to_section_id_rejected(self, tmp_path: Path) -> None:
        """B4: editing the registro field to a section-ID must raise ValueError."""
        service, _ = _build_service(tmp_path)
        with pytest.raises(ValueError, match="section"):
            service.apply_edit("g1", "registro", "4251")


# ---------------------------------------------------------------------------
# B6: reassignment is idempotent — no duplicate audit event for a no-op
# ---------------------------------------------------------------------------


class TestReassignIdempotency:
    def test_identical_reassign_twice_appends_once(self, tmp_path: Path) -> None:
        """B6: applying the same reassign twice must not duplicate the audit event."""
        service, _ = _build_service(tmp_path)
        service.apply_reassignment("g1", "R777", date(2025, 9, 1))
        first_len = len(service.get_audit_trail())
        # Identical reassign — should be a no-op (no new event)
        service.apply_reassignment("g1", "R777", date(2025, 9, 1))
        assert len(service.get_audit_trail()) == first_len


# ---------------------------------------------------------------------------
# _parse_date helper
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_none_returns_none(self) -> None:
        assert _parse_date(None) is None

    def test_date_object_passthrough(self) -> None:
        d = date(2024, 1, 15)
        assert _parse_date(d) is d

    def test_iso_string_parsed(self) -> None:
        assert _parse_date("2024-03-10") == date(2024, 3, 10)

    def test_none_string_returns_none(self) -> None:
        assert _parse_date("None") is None
        assert _parse_date("null") is None
        assert _parse_date("") is None

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse date"):
            _parse_date("not-a-date")

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Expected date"):
            _parse_date(12345)


# ---------------------------------------------------------------------------
# errored_guias constructor state + read-only property (REV-E03)
# ---------------------------------------------------------------------------


def _make_errored_guia(
    guia_id: str = "eg1",
    registro: str | None = "R001",
    source_pages: list[int] | None = None,
) -> ErroredGuia:
    return ErroredGuia(
        guia_id=guia_id,
        registro=registro,
        source_pages=source_pages or [5],
    )


class TestReviewServiceErroredGuias:
    """ReviewService holds errored_guias as read-only constructor state (REV-E03)."""

    def test_defaults_to_empty_list_when_none_passed(self, tmp_path: Path) -> None:
        """No errored_guias arg → .errored_guias returns []."""
        service, _ = _build_service(tmp_path)
        assert service.errored_guias == []

    def test_stores_supplied_list(self, tmp_path: Path) -> None:
        """errored_guias passed to __init__ are accessible via property."""
        eg = _make_errored_guia()
        ctx = RunContext(
            pdf_path=tmp_path / "in.pdf",
            output_base=tmp_path / "runs",
            run_id="eg-test",
        )
        ctx.write_review_sidecar({"edits": [], "audit_trail": []})
        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)
        service = ReviewService(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=[eg],
        )
        assert len(service.errored_guias) == 1
        assert service.errored_guias[0].guia_id == "eg1"

    def test_property_returns_copy_not_mutating_original(self, tmp_path: Path) -> None:
        """Property must return an independent copy so callers cannot mutate internal state."""
        eg = _make_errored_guia()
        ctx = RunContext(
            pdf_path=tmp_path / "in.pdf",
            output_base=tmp_path / "runs",
            run_id="eg-copy-test",
        )
        ctx.write_review_sidecar({"edits": [], "audit_trail": []})
        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)
        service = ReviewService(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=[eg],
        )
        retrieved = service.errored_guias
        retrieved.clear()
        # Internal list must be unchanged
        assert len(service.errored_guias) == 1

    def test_restore_from_sidecar_preserves_errored_guias(self, tmp_path: Path) -> None:
        """restore_from_sidecar with errored_guias param preserves the list (REV-E03 restart)."""
        eg1 = _make_errored_guia("eg1")
        eg2 = _make_errored_guia("eg2", source_pages=[11])
        ctx = RunContext(
            pdf_path=tmp_path / "in.pdf",
            output_base=tmp_path / "runs",
            run_id="eg-restart-test",
        )
        ctx.write_review_sidecar({"edits": [], "audit_trail": []})
        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        service = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=[eg1, eg2],
        )
        assert len(service.errored_guias) == 2
        ids = {eg.guia_id for eg in service.errored_guias}
        assert ids == {"eg1", "eg2"}

    def test_restore_from_sidecar_defaults_empty_when_not_passed(self, tmp_path: Path) -> None:
        """restore_from_sidecar without errored_guias arg yields []."""
        ctx = RunContext(
            pdf_path=tmp_path / "in.pdf",
            output_base=tmp_path / "runs",
            run_id="eg-empty-test",
        )
        ctx.write_review_sidecar({"edits": [], "audit_trail": []})
        guias = [_make_guia()]
        declared = [_make_registro()]
        reconciler = ReconciliationService()
        rows = reconciler.reconcile(declared, guias)

        service = ReviewService.restore_from_sidecar(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
        )
        assert service.errored_guias == []

    def test_existing_init_signature_unchanged(self, tmp_path: Path) -> None:
        """4-arg __init__ still works without errored_guias (backward-compat)."""
        service, _ = _build_service(tmp_path)
        # Verify rows still accessible — other tests depend on this shape
        assert isinstance(service.rows, list)


# ---------------------------------------------------------------------------
# T-3: add_recovered_guia (REV-R05)
# ---------------------------------------------------------------------------


class TestAddRecoveredGuia:
    """T-3: ReviewService.add_recovered_guia — append, drop from errored, re-reconcile, idempotent."""

    def _make_errored(self, guia_id: str = "errored-g1", registro: str = "R001") -> ErroredGuia:
        return ErroredGuia(registro=registro, guia_id=guia_id, source_pages=[3])

    def _make_recovered_guia(
        self,
        guia_id: str = "errored-g1",
        registro: str = "R001",
        desc: str = "acero corrugado",
        qty: str = "30",
    ) -> GuiaDeRemision:
        return GuiaDeRemision(
            guia_id=guia_id,
            registro=registro,
            fecha=date(2026, 5, 28),
            # Recovered guías ALWAYS carry requires_review=True (reconciliation gate).
            lines=[_make_line(desc=desc, qty=qty, confidence=1.0, requires_review=True)],
            source_pages=[3],
        )

    def test_add_recovered_appends_to_guias(self, tmp_path: Path) -> None:
        """Recovered guía must be added to the service's guía list."""
        guias = [_make_guia()]
        declared = [_make_registro()]
        errored = [self._make_errored()]
        service, _ = _build_service(tmp_path, guias=guias, declared=declared)
        service._errored_guias = errored  # inject for this test

        recovered = self._make_recovered_guia()
        service.add_recovered_guia(recovered)

        guia_ids = {g.guia_id for g in service.guias}
        assert "errored-g1" in guia_ids

    def test_add_recovered_removes_from_errored_guias(self, tmp_path: Path) -> None:
        """Guía_id must be removed from errored_guias list after recovery."""
        guias = [_make_guia()]
        declared = [_make_registro()]
        errored = [self._make_errored("errored-g1"), self._make_errored("errored-g2")]
        service, _ = _build_service(tmp_path, guias=guias, declared=declared)
        service._errored_guias = list(errored)

        recovered = self._make_recovered_guia("errored-g1")
        service.add_recovered_guia(recovered)

        remaining_ids = {e.guia_id for e in service.errored_guias}
        assert "errored-g1" not in remaining_ids
        assert "errored-g2" in remaining_ids  # additive isolation: other entries unaffected

    def test_add_recovered_triggers_re_reconcile(self, tmp_path: Path) -> None:
        """Re-reconcile must run after adding; rows must update (no stale state)."""
        declared_line = _make_line(desc="acero corrugado", qty="60")
        registro = _make_registro(numero="R001", lines=[declared_line])
        guia = _make_guia(guia_id="g-existing", registro="R001", lines=[_make_line(qty="30")])
        reconciler = ReconciliationService()
        rows_before = reconciler.reconcile([registro], [guia])
        service, _ = _build_service(tmp_path, guias=[guia], declared=[registro], rows=rows_before)

        # Inject a recovered guía contributing the missing 30 KG
        recovered = GuiaDeRemision(
            guia_id="errored-g1",
            registro="R001",
            fecha=date(2026, 5, 28),
            lines=[
                _make_line(
                    desc="acero corrugado", qty="30", confidence=1.0, requires_review=True
                )
            ],
            source_pages=[3],
        )
        updated_rows = service.add_recovered_guia(recovered)

        # After adding, the R001/acero corrugado/KG group should now have summed_qty=60
        target = next(
            (r for r in updated_rows if r.registro == "R001" and "acero corrugado" in r.material_canonical),
            None,
        )
        assert target is not None, "Expected a reconciliation row for R001/acero corrugado"
        assert target.summed_qty == Decimal("60"), (
            f"Expected summed_qty=60 after recovery; got {target.summed_qty}"
        )

    def test_add_recovered_idempotent(self, tmp_path: Path) -> None:
        """Calling add_recovered_guia twice with the same guia_id must be idempotent."""
        guias = [_make_guia()]
        declared = [_make_registro()]
        errored = [self._make_errored()]
        service, _ = _build_service(tmp_path, guias=guias, declared=declared)
        service._errored_guias = list(errored)

        recovered = self._make_recovered_guia()
        service.add_recovered_guia(recovered)
        service.add_recovered_guia(recovered)  # second call — should NOT add duplicate

        # Should only appear once
        count = sum(1 for g in service.guias if g.guia_id == "errored-g1")
        assert count == 1, f"Expected exactly 1 recovered guía; found {count}"

    def test_add_recovered_other_registros_unaffected(self, tmp_path: Path) -> None:
        """Adding a guía to R001 must NOT change rows for R002."""
        line_r001 = _make_line(desc="acero corrugado", qty="30")
        line_r002 = _make_line(desc="acero corrugado", qty="50")
        reg001 = _make_registro(numero="R001", lines=[line_r001])
        reg002 = _make_registro(numero="R002", lines=[line_r002])
        guia_r001 = _make_guia(guia_id="g-r001", registro="R001")
        guia_r002 = _make_guia(guia_id="g-r002", registro="R002")

        reconciler = ReconciliationService()
        initial_rows = reconciler.reconcile([reg001, reg002], [guia_r001, guia_r002])
        r002_before = next(r for r in initial_rows if r.registro == "R002")

        service, _ = _build_service(
            tmp_path,
            guias=[guia_r001, guia_r002],
            declared=[reg001, reg002],
            rows=initial_rows,
        )
        recovered = self._make_recovered_guia("errored-g1", registro="R001")
        updated = service.add_recovered_guia(recovered)

        r002_after = next(r for r in updated if r.registro == "R002")
        assert r002_after.summed_qty == r002_before.summed_qty, (
            "Additive isolation: R002 summed_qty must be unchanged"
        )
