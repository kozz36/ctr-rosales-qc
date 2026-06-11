"""Tests for ReviewService.recover_discarded_page (PR-2).

Strict TDD: written before implementation (RED).

Invariants tested:
  - Entry is removed from discarded_pages after successful recovery.
  - Fail-closed guard: raises ValueError if any line has requires_review != True.
  - Mirrors add_recovered_guia guard contract (FIX #5 parity).

Spec: REV-R31. Design: §4.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from reconciliation.domain.models import (
    DiscardedPage,
    GuiaDeRemision,
    MaterialLine,
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


def _make_guia(
    page: int = 152,
    registro: str | None = "232",
    requires_review: bool = True,
) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=f"recovered_{page}",
        registro=registro,
        fecha=None,
        fecha_entrega=None,
        lines=[_make_line(requires_review=requires_review, source_page=page)],
        source_pages=[page],
        identity_source="operator",
    )


def _make_ctx(tmp_path: Path):
    """Build a real RunContext backed by a temporary directory."""
    from reconciliation.application.run_context import RunContext

    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    output_base = tmp_path / "output"
    output_base.mkdir(parents=True, exist_ok=True)
    return RunContext(pdf_path=pdf_path, output_base=output_base, run_id="run_hook")


def _build_review_service(
    tmp_path: Path,
    discarded_pages: list[DiscardedPage] | None = None,
    guias: list[GuiaDeRemision] | None = None,
):
    """Build a real ReviewService with minimal state for hook tests."""
    from reconciliation.application.review_service import ReviewService

    ctx = _make_ctx(tmp_path)

    return ReviewService(
        declared=[],
        guias=guias or [],
        rows=[],
        ctx=ctx,
        errored_guias=[],
        discarded_pages=discarded_pages or [],
    )


# ---------------------------------------------------------------------------
# 2.1.9 — recover_discarded_page removes entry from discarded_pages
# ---------------------------------------------------------------------------


def test_recover_discarded_page_removes_entry_from_list(tmp_path: Path):
    """REV-R31 / Design §4 — entry for recovered page is removed; other entries stay.

    FAILS today: recover_discarded_page does not exist.
    """
    dp152 = DiscardedPage(page=152, registro="232", lines=[])
    dp175 = DiscardedPage(page=175, registro="233", lines=[])
    svc = _build_review_service(tmp_path, discarded_pages=[dp152, dp175])

    guia = _make_guia(page=152)
    svc.recover_discarded_page(page=152, guia=guia)

    remaining = svc.discarded_pages
    assert len(remaining) == 1
    assert remaining[0].page == 175


# ---------------------------------------------------------------------------
# 2.1.10 — fail-closed guard: ValueError on requires_review=False line
# ---------------------------------------------------------------------------


def test_recover_discarded_page_fail_closed_guard(tmp_path: Path):
    """Design §4 — ValueError raised when any line has requires_review=False.

    Mirrors add_recovered_guia fail-closed guard (FIX #5 parity).
    FAILS today: recover_discarded_page does not exist.
    """
    dp152 = DiscardedPage(page=152, registro="232", lines=[])
    svc = _build_review_service(tmp_path, discarded_pages=[dp152])

    bad_guia = _make_guia(page=152, requires_review=False)  # violates invariant

    with pytest.raises(ValueError, match="requires_review"):
        svc.recover_discarded_page(page=152, guia=bad_guia)
