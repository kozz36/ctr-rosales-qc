"""Sidecar restart round-trip for recovered_discarded_page events (PR-2).

Strict TDD: written before implementation (RED).

Design §5 / §11.1: recovery must survive a service restart.
  1. Create ReviewService with 1 discarded entry.
  2. Call recover_discarded_page(page=152, guia=...).
  3. Assert discarded_pages == [] and guia is in guias.
  4. Call restore_from_sidecar on a FRESH ReviewService with the same sidecar.
  5. Assert fresh service has discarded_pages == [] and recovered guía is present.

Spec: Design §5 (§11.1 risk — sidecar replay mandatory).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from reconciliation.domain.models import (
    DiscardedPage,
    GuiaDeRemision,
    MaterialLine,
    Registro,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_line(requires_review: bool = True, source_page: int = 152) -> MaterialLine:
    return MaterialLine(
        description_raw="BARRA A615 G60 1/2\"",
        description_canonical="BARRA A615 G60 1/2\" 9M",
        cantidad=Decimal("2.500"),
        unidad="TN",
        source_page=source_page,
        requires_review=requires_review,
        confidence=0.92,
        match_method="deterministic",
    )


def _make_recovered_guia(page: int = 152, registro: str | None = "232") -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=f"recovered_{page}",
        registro=registro,
        fecha=None,
        fecha_entrega=None,
        lines=[_make_line(source_page=page)],
        source_pages=[page],
        identity_source="operator",
    )


def _make_ctx(tmp_path: Path):
    """Build a real RunContext backed by a temporary directory."""
    from reconciliation.application.run_context import RunContext

    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")  # minimal placeholder
    output_base = tmp_path / "output"
    output_base.mkdir(parents=True, exist_ok=True)
    return RunContext(pdf_path=pdf_path, output_base=output_base, run_id="run_001")


def _build_svc(tmp_path: Path, discarded_pages=None, guias=None, rows=None, declared=None):
    from reconciliation.application.review_service import ReviewService

    ctx = _make_ctx(tmp_path)
    return ReviewService(
        declared=declared or [],
        guias=guias or [],
        rows=rows or [],
        ctx=ctx,
        errored_guias=[],
        discarded_pages=discarded_pages or [],
    ), ctx


# ---------------------------------------------------------------------------
# 2.1.17 — restart round-trip for recovered_discarded_page
# ---------------------------------------------------------------------------


def test_restart_round_trip_recovered_discarded_page(tmp_path: Path):
    """Design §5 (§11.1 risk) — recovered_discarded_page survives a sidecar restart.

    Steps:
      1. ReviewService with 1 discarded entry (page=152).
      2. recover_discarded_page → discarded list becomes empty; guia added.
      3. Read persisted sidecar events.
      4. Restore fresh ReviewService from sidecar.
      5. Fresh service: discarded_pages==[], guia 'recovered_152' present.

    FAILS today: recovered_discarded_page audit event not replayed by restore_from_sidecar.
    """
    from reconciliation.application.review_service import ReviewService

    dp = DiscardedPage(page=152, registro="232", lines=[])
    svc, ctx = _build_svc(tmp_path, discarded_pages=[dp])

    guia = _make_recovered_guia(page=152)
    svc.recover_discarded_page(page=152, guia=guia)

    # Assert in-memory state after recovery
    assert svc.discarded_pages == []
    guia_ids = [g.guia_id for g in svc.guias]
    assert "recovered_152" in guia_ids

    # Load sidecar via ctx and verify the event is present
    sidecar_data = ctx.read_review_sidecar()
    sidecar_events = sidecar_data.get("edits", [])
    kinds = [e.get("kind") for e in sidecar_events]
    assert "recovered_discarded_page" in kinds, (
        f"Expected 'recovered_discarded_page' in sidecar events; got: {kinds}"
    )

    # Rebuild ReviewService from same initial state + sidecar replay
    fresh_svc = ReviewService.restore_from_sidecar(
        declared=[],
        guias=[],       # start from initial state (no guía yet)
        rows=[],
        ctx=ctx,
        errored_guias=[],
        discarded_pages=[dp],   # start from original discarded entry
    )

    # Sidecar replay must restore: discarded_pages == [], recovered guía present
    assert fresh_svc.discarded_pages == [], (
        "After sidecar replay, discarded_pages must be empty"
    )
    restored_guia_ids = [g.guia_id for g in fresh_svc.guias]
    assert "recovered_152" in restored_guia_ids, (
        f"Recovered guía must be present after restart; got: {restored_guia_ids}"
    )
