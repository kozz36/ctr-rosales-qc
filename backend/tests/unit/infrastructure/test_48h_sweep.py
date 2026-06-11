"""Unit tests for the 48-hour failed-run sweep.

Spec: RH-008, D4, D5.
TDD Phase: RED — written before implementation.

Covers both the adapter-level sweep semantics and the GET /runs lazy-sweep trigger.
"""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from reconciliation.application.config import AppConfig
from reconciliation.infrastructure.api.main import create_app


def _fresh_run_id() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


def _write_manifest(run_dir: Path, run_id: str, status: str, completed_at: str) -> None:
    """Write a minimal run_manifest.json for sweep testing."""
    data = {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "started_at": completed_at,
        "completed_at": completed_at,
        "seq": 1,
        "registro_min": None,
        "registro_max": None,
        "row_count": 0,
        "match_count": 0,
        "mismatch_count": 0,
        "warnings": [],
        "vision_calls_made": 0,
        "error": "crashed" if status == "error" else None,
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _make_client(tmp_path: Path) -> TestClient:
    from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
        JsonManifestRunHistoryAdapter,
    )

    app = create_app()
    config = AppConfig(output_dir=tmp_path / "runs")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    app.state.config = config
    app.state.run_registry = {}
    app.state.run_history = JsonManifestRunHistoryAdapter()
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 2.1.13 — sweep_failed deletes old error-status run dir
# ---------------------------------------------------------------------------


class TestSweepDeletesOldFailed:
    """sweep_failed removes a failed run older than cutoff."""

    def test_sweep_deletes_old_failed_run(self, tmp_path: Path) -> None:
        """Failed run with completed_at 49h ago is swept; dir deleted.

        FAILS before implementation: GET /runs sweep wiring may not be present,
        and the adapter may already handle this — but the test confirms the
        registry entry is also removed via GET /runs. Tests adapter directly.
        Spec: RH-008-S01.
        """
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)

        run_id = _fresh_run_id()
        run_dir = output_dir / run_id
        run_dir.mkdir()

        old_ts = _utc_now() - datetime.timedelta(hours=49)
        _write_manifest(run_dir, run_id, "error", _iso(old_ts))

        cutoff = _utc_now() - datetime.timedelta(hours=48)
        deleted = adapter.sweep_failed(output_dir, cutoff)

        assert run_id in deleted, f"expected {run_id} in deleted list; got {deleted}"
        assert not run_dir.exists(), f"run dir {run_dir} should be deleted"


# ---------------------------------------------------------------------------
# 2.1.14 — sweep_failed never deletes completed run
# ---------------------------------------------------------------------------


class TestSweepNeverDeletesCompleted:
    """sweep_failed NEVER deletes a review-status run, even if old."""

    def test_sweep_never_deletes_completed_run(self, tmp_path: Path) -> None:
        """Completed (review) run with old timestamp is NOT swept.

        Spec: RH-008-S02 (only error-status runs are swept).
        """
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)

        run_id = _fresh_run_id()
        run_dir = output_dir / run_id
        run_dir.mkdir()

        old_ts = _utc_now() - datetime.timedelta(hours=200)
        _write_manifest(run_dir, run_id, "review", _iso(old_ts))

        cutoff = _utc_now() - datetime.timedelta(hours=48)
        deleted = adapter.sweep_failed(output_dir, cutoff)

        assert run_id not in deleted, "completed run must NEVER be auto-deleted"
        assert run_dir.exists(), "completed run dir must survive sweep"


# ---------------------------------------------------------------------------
# 2.1.15 — sweep_failed keeps a recent failed run
# ---------------------------------------------------------------------------


class TestSweepKeepsRecentFailed:
    """Recent failed run (23h ago) is NOT swept."""

    def test_sweep_keeps_recent_failed_run(self, tmp_path: Path) -> None:
        """Failed run completed 23h ago (within 48h cutoff) is NOT deleted.

        Spec: RH-008-S03.
        """
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)

        run_id = _fresh_run_id()
        run_dir = output_dir / run_id
        run_dir.mkdir()

        recent_ts = _utc_now() - datetime.timedelta(hours=23)
        _write_manifest(run_dir, run_id, "error", _iso(recent_ts))

        cutoff = _utc_now() - datetime.timedelta(hours=48)
        deleted = adapter.sweep_failed(output_dir, cutoff)

        assert run_id not in deleted, "recent failed run must NOT be swept"
        assert run_dir.exists(), "recent failed run dir must survive"


# ---------------------------------------------------------------------------
# 2.1.16 — sweep_failed ignores non-run dirs
# ---------------------------------------------------------------------------


class TestSweepIgnoresNonRunDirs:
    """sweep_failed does not touch non-UUID directories in output_dir."""

    def test_sweep_ignores_non_run_dirs(self, tmp_path: Path) -> None:
        """Non-UUID dir alongside an old failed run — only the run is swept.

        Spec: RH-008-S04.
        """
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)

        # Old failed run
        run_id = _fresh_run_id()
        run_dir = output_dir / run_id
        run_dir.mkdir()
        old_ts = _utc_now() - datetime.timedelta(hours=50)
        _write_manifest(run_dir, run_id, "error", _iso(old_ts))

        # Non-UUID dir — must survive
        unrelated = output_dir / "not-a-uuid-dir"
        unrelated.mkdir()
        (unrelated / "somefile.txt").write_text("keep me", encoding="utf-8")

        cutoff = _utc_now() - datetime.timedelta(hours=48)
        deleted = adapter.sweep_failed(output_dir, cutoff)

        assert run_id in deleted, "old failed run must be swept"
        assert unrelated.exists(), "unrelated non-UUID dir must NOT be touched"


# ---------------------------------------------------------------------------
# 2.1.17 — GET /runs triggers sweep (removes old failed run from response)
# ---------------------------------------------------------------------------


class TestGetRunsTriggersSweep:
    """GET /runs triggers 48h sweep; old failed run disappears from registry + response."""

    def test_get_runs_triggers_sweep(self, tmp_path: Path) -> None:
        """GET /runs sweeps a >48h old failed run; entry absent from response.

        FAILS before 2.2.4: either GET /runs doesn't sweep, or the sweep doesn't
        remove entries from the registry dict passed to the endpoint.
        Spec: RH-008, D4.
        """
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()
        config = client.app.state.config  # type: ignore[attr-defined]

        # Create the run dir on disk with a manifest marking status=error, 49h old
        run_dir = config.output_dir / run_id
        run_dir.mkdir(parents=True)
        old_ts = _utc_now() - datetime.timedelta(hours=49)
        _write_manifest(run_dir, run_id, "error", _iso(old_ts))

        # Seed the registry entry (as if lifespan scan populated it)
        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "error",
            "started_at": _iso(old_ts),
            "completed_at": _iso(old_ts),
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
            "error": "crashed",
        }

        # GET /runs must trigger sweep and return the entry deleted from the result
        resp = client.get("/api/v1/runs")

        assert resp.status_code == 200, f"GET /runs failed: {resp.text}"
        data = resp.json()

        run_ids_in_response = [item["run_id"] for item in data]
        assert run_id not in run_ids_in_response, (
            f"old failed run {run_id} should have been swept from GET /runs response"
        )
        # Also verify it was removed from registry
        assert run_id not in client.app.state.run_registry, (  # type: ignore[attr-defined]
            "old failed run must be removed from run_registry by GET /runs sweep"
        )
        # And from disk
        assert not run_dir.exists(), (
            "old failed run dir must be deleted from disk by GET /runs sweep"
        )
