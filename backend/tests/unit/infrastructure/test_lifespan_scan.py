"""Unit tests for lifespan scan and GET /runs endpoint.

Spec: RH-002, RH-003, RH-006-S01.
TDD Phase: GREEN — tests written against the real implementation.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest


def _fresh_run_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 1.1.17 — lifespan scan populates registry
# ---------------------------------------------------------------------------


class TestLifespanScan:
    """Lifespan scan merges run dirs into run_registry (RH-002, RH-006-S01)."""

    def test_lifespan_scan_populates_registry(self, tmp_path: Path) -> None:
        """Lifespan scan merges 3 dirs (manifest, legacy-cache, pdf-only) into registry."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )
        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

        adapter = JsonManifestRunHistoryAdapter()

        # Dir 1: valid manifest
        r1 = _fresh_run_id()
        (tmp_path / r1).mkdir()
        adapter.write_manifest(
            RunManifest(
                schema_version=1, run_id=r1, status="review",
                started_at="2026-06-11T00:00:00+00:00", completed_at=None,
                seq=1, registro_min=None, registro_max=None,
                row_count=0, match_count=0, mismatch_count=0,
                warnings=[], vision_calls_made=0,
            ),
            tmp_path,
        )

        # Dir 2: legacy (extraction_cache only)
        r2 = _fresh_run_id()
        (tmp_path / r2).mkdir()
        (tmp_path / r2 / "extraction_cache.json").write_text("{}", encoding="utf-8")

        # Dir 3: pdf-only
        r3 = _fresh_run_id()
        (tmp_path / r3).mkdir()
        (tmp_path / r3 / f"{r3}.pdf").write_bytes(b"%PDF-1.4")

        # Simulate what lifespan will do: scan + merge into registry
        run_registry: dict[str, Any] = {}
        entries = adapter.scan(tmp_path)
        for entry in entries:
            run_registry[entry["run_id"]] = entry

        assert len(run_registry) == 3, f"expected 3 entries, got {len(run_registry)}"
        assert r1 in run_registry
        assert r2 in run_registry
        assert r3 in run_registry
        assert all(e["hydrated"] is False for e in run_registry.values())


# ---------------------------------------------------------------------------
# GET /runs sorted response tests (via TestClient)
# ---------------------------------------------------------------------------


class TestGetRunsEndpoint:
    """GET /runs returns sorted list (RH-003)."""

    def _get_test_app_with_registry(self, registry: dict[str, Any]) -> Any:
        """Create a test app with pre-populated registry (bypassing lifespan scan)."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        app = create_app()

        # Override the lifespan to avoid the real scan on real backend/runs
        # We'll patch the scan to return empty so the real dirs don't interfere
        return app, registry

    def test_get_runs_returns_sorted_newest_first(self) -> None:
        """GET /runs returns entries sorted by started_at descending (RH-003-S01)."""
        from unittest.mock import patch  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        app = create_app()

        r1 = _fresh_run_id()
        r2 = _fresh_run_id()
        r3 = _fresh_run_id()

        entries = {
            r1: {
                "run_id": r1, "status": "review",
                "started_at": "2026-06-11T00:00:00+00:00", "completed_at": None,
                "seq": 1, "registro_min": None, "registro_max": None,
                "row_count": 0, "match_count": 0, "mismatch_count": 0,
                "warnings": [], "vision_calls_made": 0, "degraded": False,
                "hydrated": False, "error": None,
            },
            r2: {
                "run_id": r2, "status": "review",
                "started_at": "2026-06-11T02:00:00+00:00", "completed_at": None,
                "seq": 2, "registro_min": None, "registro_max": None,
                "row_count": 0, "match_count": 0, "mismatch_count": 0,
                "warnings": [], "vision_calls_made": 0, "degraded": False,
                "hydrated": False, "error": None,
            },
            r3: {
                "run_id": r3, "status": "review",
                "started_at": "2026-06-11T01:00:00+00:00", "completed_at": None,
                "seq": 3, "registro_min": None, "registro_max": None,
                "row_count": 0, "match_count": 0, "mismatch_count": 0,
                "warnings": [], "vision_calls_made": 0, "degraded": False,
                "hydrated": False, "error": None,
            },
        }

        # Patch the scan to return empty (override the startup scan)
        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ):
            with TestClient(app) as client:
                # Override registry AFTER lifespan
                app.state.run_registry.update(entries)
                resp = client.get("/api/v1/runs")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) == 3
        times = [item["started_at"] for item in data]
        assert times == sorted(times, reverse=True), f"not sorted desc: {times}"

    def test_get_runs_failed_run_appears_with_error_flag(self) -> None:
        """GET /runs includes error-status run with error indicator (RH-003-S02)."""
        from unittest.mock import patch  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        app = create_app()
        run_id = _fresh_run_id()
        error_entry = {
            "run_id": run_id, "status": "error",
            "started_at": "2026-06-11T00:00:00+00:00", "completed_at": None,
            "seq": 1, "registro_min": None, "registro_max": None,
            "row_count": 0, "match_count": 0, "mismatch_count": 0,
            "warnings": [], "vision_calls_made": 0, "degraded": False,
            "hydrated": False, "error": "pipeline crashed",
        }

        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ):
            with TestClient(app) as client:
                app.state.run_registry[run_id] = error_entry
                resp = client.get("/api/v1/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "error"
        assert data[0]["error"] == "pipeline crashed"

    def test_get_runs_legacy_run_appears_last(self) -> None:
        """Legacy run (no started_at) appears after manifest runs (RH-003-S03)."""
        from unittest.mock import patch  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

        app = create_app()

        run_a = _fresh_run_id()
        run_b = _fresh_run_id()
        run_legacy = _fresh_run_id()

        registry_entries = {
            run_a: {
                "run_id": run_a, "status": "review",
                "started_at": "2026-06-11T01:00:00+00:00", "completed_at": None,
                "seq": 1, "registro_min": None, "registro_max": None,
                "row_count": 0, "match_count": 0, "mismatch_count": 0,
                "warnings": [], "vision_calls_made": 0, "degraded": False,
                "hydrated": False, "error": None,
            },
            run_b: {
                "run_id": run_b, "status": "review",
                "started_at": "2026-06-11T02:00:00+00:00", "completed_at": None,
                "seq": 2, "registro_min": None, "registro_max": None,
                "row_count": 0, "match_count": 0, "mismatch_count": 0,
                "warnings": [], "vision_calls_made": 0, "degraded": False,
                "hydrated": False, "error": None,
            },
            run_legacy: {
                "run_id": run_legacy, "status": "review",
                "started_at": None,  # legacy — no timestamp
                "completed_at": None, "seq": None,
                "registro_min": None, "registro_max": None,
                "row_count": 0, "match_count": 0, "mismatch_count": 0,
                "warnings": [], "vision_calls_made": 0, "degraded": True,
                "hydrated": False, "error": None,
            },
        }

        with patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.scan",
            return_value=[],
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter.sweep_failed",
            return_value=[],
        ):
            with TestClient(app) as client:
                app.state.run_registry.update(registry_entries)
                resp = client.get("/api/v1/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        # Last entry must be the legacy one (null started_at)
        assert data[-1]["run_id"] == run_legacy, f"expected legacy run last, got {data[-1]['run_id']}"
