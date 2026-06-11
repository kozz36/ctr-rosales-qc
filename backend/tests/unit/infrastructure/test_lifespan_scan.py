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


# ---------------------------------------------------------------------------
# RH-006-S01 — REAL lifespan e2e: restart durability (no mock theatre)
# ---------------------------------------------------------------------------


class TestLifespanRealE2E:
    """The lifespan startup MUST hydrate app.state.run_registry from a real
    output dir via create_app()'s actual lifespan — NOT a re-implemented merge.

    This is the restart-durability lock (RH-006-S01): point config at a tmp
    output dir holding (a) a manifest run, (b) a legacy cache-only dir, and
    (c) a corrupt manifest; drive the REAL lifespan through TestClient; assert
    the registry is populated, degraded flags are correct, the corrupt dir is
    skipped, and every hydrated entry carries hydrated=False.
    """

    def test_real_lifespan_hydrates_registry_from_disk(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)

        # (a) valid manifest run
        r_manifest = _fresh_run_id()
        (output_dir / r_manifest).mkdir()
        JsonManifestRunHistoryAdapter().write_manifest(
            RunManifest(
                schema_version=1, run_id=r_manifest, status="review",
                started_at="2026-06-11T00:00:00+00:00", completed_at=None,
                seq=1, registro_min=None, registro_max=None,
                row_count=0, match_count=0, mismatch_count=0,
                warnings=[], vision_calls_made=0,
            ),
            output_dir,
        )

        # (b) legacy cache-only dir → degraded review
        r_legacy = _fresh_run_id()
        (output_dir / r_legacy).mkdir()
        (output_dir / r_legacy / "extraction_cache.json").write_text("{}", encoding="utf-8")

        # (c) corrupt manifest → skipped by scan
        r_corrupt = _fresh_run_id()
        (output_dir / r_corrupt).mkdir()
        (output_dir / r_corrupt / "run_manifest.json").write_text(
            "{not valid json", encoding="utf-8"
        )

        # Point the REAL lifespan at the tmp output dir; no yaml file so only
        # env + defaults apply (RECONCILIATION_CONFIG → nonexistent path).
        monkeypatch.setenv("RECONCILIATION_CONFIG", str(tmp_path / "no-such-config.yaml"))
        monkeypatch.setenv("RECONCILIATION__OUTPUT_DIR", str(output_dir))

        app = create_app()
        with TestClient(app) as client:
            registry = client.app.state.run_registry  # type: ignore[attr-defined]

            # Corrupt dir skipped → only 2 entries hydrated.
            assert set(registry.keys()) == {r_manifest, r_legacy}, (
                f"unexpected registry keys: {sorted(registry.keys())}"
            )
            assert r_corrupt not in registry

            # Manifest entry: full, degraded=False.
            assert registry[r_manifest]["degraded"] is False
            assert registry[r_manifest]["status"] == "review"

            # Legacy cache-only entry: degraded=True, status review.
            assert registry[r_legacy]["degraded"] is True
            assert registry[r_legacy]["status"] == "review"

            # Restart-hydration invariant: nothing is live yet.
            assert all(e["hydrated"] is False for e in registry.values())

            # The single shared adapter is on app.state (D1).
            assert client.app.state.run_history is not None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# F-1 — pending-zombie: scan coerces in-flight statuses to error on restart
# ---------------------------------------------------------------------------


class TestPendingZombieCoercion:
    """A crash/shutdown mid-retry leaves the on-disk manifest status='pending'.

    On restart a fresh process cannot own any in-flight run, yet the stale
    'pending' (or 'processing') manifest makes the run unrecoverable via the
    API: DELETE 409 (in-flight guard), RETRY 409 (not error), sweep skips it
    (error-only). The scan path MUST coerce such statuses to 'error' with an
    honest error string so the run becomes retryable + deletable + sweepable.
    """

    def test_real_lifespan_coerces_pending_manifest_to_error(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """Real lifespan against a tmp dir holding a 'pending' manifest surfaces
        the entry as status='error' with an interrupted-by-restart error, and
        the coerced run is retryable (202) and deletable (204).

        FAILS before F-1: scan returns status='pending' verbatim → DELETE 409,
        RETRY 409 — unrecoverable.
        """
        from unittest.mock import patch  # noqa: PLC0415

        from fastapi.testclient import TestClient  # noqa: PLC0415

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)

        # A run interrupted mid-retry: production path is write a manifest then
        # mark_pending (which writes status='pending' as raw JSON, bypassing the
        # pydantic Literal), then a crash before completion. Reproduce exactly.
        r_pending = _fresh_run_id()
        (output_dir / r_pending).mkdir()
        adapter = JsonManifestRunHistoryAdapter()
        adapter.write_manifest(
            RunManifest(
                schema_version=1, run_id=r_pending, status="error",
                started_at="2026-06-11T00:00:00+00:00", completed_at=None,
                seq=1, registro_min=None, registro_max=None,
                row_count=0, match_count=0, mismatch_count=0,
                warnings=[], vision_calls_made=0,
            ),
            output_dir,
            force_seq=1,
        )
        adapter.mark_pending(r_pending, output_dir)  # → status='pending' on disk
        (output_dir / r_pending / f"{r_pending}.pdf").write_bytes(b"%PDF-1.4")

        monkeypatch.setenv("RECONCILIATION_CONFIG", str(tmp_path / "no-such-config.yaml"))
        monkeypatch.setenv("RECONCILIATION__OUTPUT_DIR", str(output_dir))

        app = create_app()
        with TestClient(app) as client:
            registry = client.app.state.run_registry  # type: ignore[attr-defined]

            # Coerced to error on scan.
            assert r_pending in registry
            assert registry[r_pending]["status"] == "error", (
                f"pending manifest must be coerced to error on restart; "
                f"got {registry[r_pending]['status']!r}"
            )
            assert registry[r_pending]["error"], "coerced run must carry an honest error string"

            # On-disk manifest rewritten so GET /runs consistency + sweep eligibility.
            manifest_path = output_dir / r_pending / "run_manifest.json"
            disk = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert disk["status"] == "error", "coerced status must be persisted to disk"

            # Retry now valid (no longer 409).
            with patch(
                "reconciliation.infrastructure.api.routes._run_pipeline_background"
            ):
                r_retry = client.post(f"/api/v1/runs/{r_pending}/retry")
            assert r_retry.status_code == 202, (
                f"coerced run must be retryable; got {r_retry.status_code}: {r_retry.text}"
            )

    def test_real_lifespan_coerced_run_is_deletable(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """A 'pending' manifest coerced to error on restart can be DELETEd (204).

        FAILS before F-1: status='pending' → DELETE 409 (in-flight guard).
        """
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
        from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)

        r_pending = _fresh_run_id()
        (output_dir / r_pending).mkdir()
        JsonManifestRunHistoryAdapter().write_manifest(
            RunManifest(
                schema_version=1, run_id=r_pending, status="error",
                started_at="2026-06-11T00:00:00+00:00", completed_at=None,
                seq=1, registro_min=None, registro_max=None,
                row_count=0, match_count=0, mismatch_count=0,
                warnings=[], vision_calls_made=0,
            ),
            output_dir,
            force_seq=1,
        )
        # Simulate a 'processing' on-disk state (raw JSON; pydantic Literal forbids it).
        manifest_path = output_dir / r_pending / "run_manifest.json"
        _data = json.loads(manifest_path.read_text(encoding="utf-8"))
        _data["status"] = "processing"
        manifest_path.write_text(json.dumps(_data), encoding="utf-8")

        monkeypatch.setenv("RECONCILIATION_CONFIG", str(tmp_path / "no-such-config.yaml"))
        monkeypatch.setenv("RECONCILIATION__OUTPUT_DIR", str(output_dir))

        app = create_app()
        with TestClient(app) as client:
            resp = client.delete(f"/api/v1/runs/{r_pending}")
            assert resp.status_code == 204, (
                f"coerced (processing→error) run must be deletable; "
                f"got {resp.status_code}: {resp.text}"
            )
            assert not (output_dir / r_pending).exists()
