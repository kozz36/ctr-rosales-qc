"""Unit tests for lazy hydration dependency (_get_hydrated_entry).

Spec: RH-005, RH-011-S01, D4.
TDD Phase: RED — written before implementation.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from reconciliation.application.config import AppConfig
from reconciliation.infrastructure.api.main import create_app


def _fresh_run_id() -> str:
    return str(uuid.uuid4())


def _make_app(tmp_path: Path) -> Any:
    """Create a TestClient-ready app with isolated state (no real lifespan scan)."""
    from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
        JsonManifestRunHistoryAdapter,
    )

    app = create_app()
    config = AppConfig(output_dir=tmp_path / "runs")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    app.state.config = config
    app.state.run_registry = {}
    app.state.run_history = JsonManifestRunHistoryAdapter()
    return app


def _make_client(tmp_path: Path) -> TestClient:
    return TestClient(_make_app(tmp_path), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 2.1.1 — GET /runs/{id} does NOT trigger hydration (cold polling path)
# ---------------------------------------------------------------------------


class TestGetRunsIdNoHydration:
    """GET /runs/{id} (status poll) serves manifest fields without triggering
    build_review_service for unhydrated entries (D4 cold-load path)."""

    def test_get_runs_id_no_hydration_served_from_manifest(self, tmp_path: Path) -> None:
        """GET /runs/{id} returns 200 from manifest fields; review_service stays None.

        FAILS before 2.2.5: the endpoint may try to call _require_review_service
        or trigger hydration, returning 409 instead of the manifest summary.
        """
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()

        # Seed an unhydrated entry with manifest fields but no review_service
        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "review",
            "started_at": "2026-06-11T00:00:00+00:00",
            "completed_at": "2026-06-11T01:00:00+00:00",
            "seq": 1,
            "registro_min": "100",
            "registro_max": "120",
            "row_count": 5,
            "match_count": 4,
            "mismatch_count": 1,
            "warnings": [],
            "vision_calls_made": 3,
            "degraded": False,
            "hydrated": False,
            "error": None,
            # No review_service, no ctx
        }

        resp = client.get(f"/api/v1/runs/{run_id}")

        assert resp.status_code == 200, f"expected 200 from manifest, got {resp.status_code}: {resp.text}"
        # review_service should still be None in registry (no hydration triggered)
        registry = client.app.state.run_registry  # type: ignore[attr-defined]
        assert registry[run_id].get("review_service") is None, (
            "GET /runs/{id} must NOT trigger hydration; review_service should remain None"
        )


# ---------------------------------------------------------------------------
# 2.1.2 — GET /runs/{id}/table triggers lazy hydration exactly once
# ---------------------------------------------------------------------------


class TestGetTableTriggersLazyHydration:
    """GET /runs/{id}/table triggers build_review_service for unhydrated entry."""

    def test_get_table_triggers_lazy_hydration(self, tmp_path: Path) -> None:
        """GET /runs/{id}/table calls build_review_service; registry gains review_service;
        hydrated becomes True.

        FAILS before 2.2.1: get_table uses _require_review_service which raises 409
        for unhydrated entries (the 'from a previous session' guard in PR-1).
        """
        app = _make_app(tmp_path)
        run_id = _fresh_run_id()

        mock_review_service = MagicMock()
        mock_review_service.rows = []
        mock_review_service.guias = []
        mock_review_service.errored_guias = []
        mock_review_service.discarded_pages = []

        # Simulate a RunContext on disk with an extraction_cache.json
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "extraction_cache.json").write_text(
            '{"declared":[],"guias":[],"rows":[],"errored_guias":[],"discarded_pages":[]}',
            encoding="utf-8",
        )
        pdf_path = run_dir / f"{run_id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        # Build a minimal RunContext for the hydration dep.
        # RunContext(pdf_path, output_base, run_id) creates run_dir under output_base.
        from reconciliation.application.run_context import RunContext  # noqa: PLC0415

        ctx = RunContext(pdf_path=pdf_path, output_base=tmp_path / "runs", run_id=run_id)

        call_count = {"n": 0}

        def fake_build_review_service(ctx_arg: Any, **_kw: Any) -> MagicMock:
            call_count["n"] += 1
            return mock_review_service

        with patch(
            "reconciliation.infrastructure.container.build_review_service",
            side_effect=fake_build_review_service,
        ), patch(
            "reconciliation.infrastructure.container.build_reprocess_service",
            return_value=MagicMock(),
        ):
            with TestClient(app, raise_server_exceptions=True) as client:
                # Seed AFTER lifespan runs (lifespan resets run_registry to {})
                client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
                    "run_id": run_id,
                    "status": "review",
                    "started_at": "2026-06-11T00:00:00+00:00",
                    "completed_at": "2026-06-11T01:00:00+00:00",
                    "seq": 1,
                    "registro_min": None,
                    "registro_max": None,
                    "row_count": 0,
                    "match_count": 0,
                    "mismatch_count": 0,
                    "warnings": [],
                    "vision_calls_made": 0,
                    "degraded": False,
                    "hydrated": False,
                    "error": None,
                    "ctx": ctx,
                }
                resp = client.get(f"/api/v1/runs/{run_id}/table")

        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        assert call_count["n"] == 1, (
            f"build_review_service must be called exactly once; was called {call_count['n']} times"
        )
        registry = app.state.run_registry
        assert registry[run_id].get("review_service") is mock_review_service, (
            "hydration must cache review_service into registry entry"
        )
        assert registry[run_id].get("hydrated") is True, (
            "registry entry must be marked hydrated=True after hydration"
        )


# ---------------------------------------------------------------------------
# 2.1.3 — Second call to GET /runs/{id}/table does not re-hydrate
# ---------------------------------------------------------------------------


class TestGetTableSecondCallNoReHyd:
    """Second GET /runs/{id}/table call skips build_review_service (already hydrated)."""

    def test_get_table_second_call_no_rehyd(self, tmp_path: Path) -> None:
        """GET /runs/{id}/table called twice; build_review_service called exactly once.

        FAILS before 2.2.1: without hydration caching, build_review_service would be
        called on every request (or the 409 guard would still fire).
        """
        app = _make_app(tmp_path)
        run_id = _fresh_run_id()

        mock_review_service = MagicMock()
        mock_review_service.rows = []
        mock_review_service.guias = []
        mock_review_service.errored_guias = []
        mock_review_service.discarded_pages = []

        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "extraction_cache.json").write_text(
            '{"declared":[],"guias":[],"rows":[],"errored_guias":[],"discarded_pages":[]}',
            encoding="utf-8",
        )
        pdf_path = run_dir / f"{run_id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        from reconciliation.application.run_context import RunContext  # noqa: PLC0415

        ctx = RunContext(pdf_path=pdf_path, output_base=tmp_path / "runs", run_id=run_id)

        call_count = {"n": 0}

        def fake_build_review_service(ctx_arg: Any, **_kw: Any) -> MagicMock:
            call_count["n"] += 1
            return mock_review_service

        with patch(
            "reconciliation.infrastructure.container.build_review_service",
            side_effect=fake_build_review_service,
        ), patch(
            "reconciliation.infrastructure.container.build_reprocess_service",
            return_value=MagicMock(),
        ):
            with TestClient(app, raise_server_exceptions=True) as client:
                # Seed AFTER lifespan runs (lifespan resets run_registry to {})
                client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
                    "run_id": run_id,
                    "status": "review",
                    "started_at": "2026-06-11T00:00:00+00:00",
                    "hydrated": False,
                    "error": None,
                    "ctx": ctx,
                    "row_count": 0, "match_count": 0, "mismatch_count": 0,
                    "warnings": [], "vision_calls_made": 0,
                }
                r1 = client.get(f"/api/v1/runs/{run_id}/table")
                r2 = client.get(f"/api/v1/runs/{run_id}/table")

        assert r1.status_code == 200, f"first call: {r1.status_code} {r1.text}"
        assert r2.status_code == 200, f"second call: {r2.status_code} {r2.text}"
        assert call_count["n"] == 1, (
            f"build_review_service must NOT be called on second request; "
            f"was called {call_count['n']} times"
        )
