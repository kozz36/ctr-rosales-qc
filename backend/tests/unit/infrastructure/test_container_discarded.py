"""Unit tests for discarded_pages hydration in build_review_service (EXT-035).

STRICT TDD: tests MUST be RED before container.py is changed.
Tests fail because ReviewService has no discarded_pages parameter yet.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
    PageClassification,
    ReconciliationRow,
    Registro,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_line(desc: str = "BARRA 3/8", qty: str = "1.0") -> MaterialLine:
    return MaterialLine(
        description_raw=desc,
        description_canonical=desc.lower(),
        unidad="TN",
        cantidad=Decimal(qty),
    )


def _make_cache(
    *,
    discarded_pages: list[dict] | None = None,
    include_discarded_key: bool = True,
) -> dict:
    """Build a minimal extraction cache dict for testing hydration."""
    cache: dict = {
        "run_id": "test-run",
        "classifications": [],
        "declared": [],
        "guias": [],
        "rows": [],
        "errored_guias": [],
    }
    if include_discarded_key:
        cache["discarded_pages"] = discarded_pages or []
    return cache


def _write_cache_and_sidecar(tmp_path: Path, cache: dict) -> "RunContext":
    """Write cache + blank sidecar; return a RunContext pointing at them."""
    from reconciliation.application.run_context import RunContext

    run_id = "test-" + uuid.uuid4().hex[:8]
    # Create the RunContext first so it creates the run directory
    ctx = RunContext(pdf_path=tmp_path / "in.pdf", output_base=tmp_path / "runs", run_id=run_id)
    ctx.extraction_cache.write_text(json.dumps(cache))
    ctx.review_sidecar.write_text(json.dumps({"edits": []}))
    return ctx


# ---------------------------------------------------------------------------
# 1.1.8 — build_review_service hydrates discarded_pages from cache
# ---------------------------------------------------------------------------


class TestBuildReviewServiceHydratesDiscardedPages:
    def test_build_review_service_hydrates_discarded_pages(self, tmp_path: Path) -> None:
        """build_review_service must hydrate discarded_pages from the cache entry
        and make them accessible via ReviewService.discarded_pages.

        Spec: EXT-035. Design: §5.
        FAILS (RED): ReviewService has no discarded_pages parameter/property yet.
        """
        from reconciliation.domain.models import DiscardedPage  # type: ignore[attr-defined]
        from reconciliation.infrastructure.container import build_review_service

        cache = _make_cache(
            discarded_pages=[
                {"page": 152, "registro": "232", "lines": []}
            ]
        )
        ctx = _write_cache_and_sidecar(tmp_path, cache)
        review_service = build_review_service(ctx)

        assert hasattr(review_service, "discarded_pages"), (
            "ReviewService must expose a discarded_pages property"
        )
        dp = review_service.discarded_pages
        assert len(dp) == 1
        entry = dp[0]
        assert isinstance(entry, DiscardedPage)
        assert entry.page == 152
        assert entry.registro == "232"
        assert entry.lines == []

    # 1.1.9 — old cache (no discarded_pages key) must hydrate to [] without error
    def test_build_review_service_old_cache_discarded_defaults_to_empty(
        self, tmp_path: Path
    ) -> None:
        """An old extraction cache without the 'discarded_pages' key must produce
        ReviewService.discarded_pages == [] without raising KeyError or ValidationError.

        Spec: EXT-035 / EXT-S035b. Backward compat.
        """
        from reconciliation.infrastructure.container import build_review_service

        cache = _make_cache(include_discarded_key=False)  # Old cache — no key
        ctx = _write_cache_and_sidecar(tmp_path, cache)
        review_service = build_review_service(ctx)

        assert review_service.discarded_pages == [], (
            "Old cache (no discarded_pages key) must default to []"
        )
