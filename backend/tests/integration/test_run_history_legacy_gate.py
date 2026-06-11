"""Real-data gate: scan the actual backend/runs/ legacy directories.

Spec: RH-002, RH-006 (legacy dirs degrade gracefully — never crash or hide).

This test uses the real backend/runs/ directory on disk — it is deterministic
and read-only (never mutates real dirs).  The corrupt-manifest case uses a
temp-dir copy.

Run:
    cd backend && uv run pytest tests/integration/test_run_history_legacy_gate.py -v
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest


# Path to the real run dirs (relative resolution from the test runner cwd=backend/)
_RUNS_DIR = Path(__file__).parent.parent.parent / "runs"


def _real_runs_dir() -> Path:
    """Return the real backend/runs directory, skipping if absent."""
    return _RUNS_DIR


# ---------------------------------------------------------------------------
# Gate 1: no crash, all dirs listed, degraded fields for manifest-less dirs
# ---------------------------------------------------------------------------


class TestScanLegacyDirsNoCrash:
    """Scan the REAL backend/runs/ directory — must not crash (RH-002, RH-006)."""

    def test_scan_legacy_dirs_no_crash(self) -> None:
        """scan(runs/) returns list; each entry has run_id, status, degraded. (RH-002, RH-006)"""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        runs_dir = _real_runs_dir()
        if not runs_dir.is_dir():
            pytest.skip(f"backend/runs/ not found at {runs_dir}")

        adapter = JsonManifestRunHistoryAdapter()
        result = adapter.scan(runs_dir)

        # Must not raise — already implied by reaching here
        assert isinstance(result, list), "scan must return a list"

        # At least some non-empty dirs exist (33 non-empty dirs detected in pre-work)
        # We only count entries that the scan found (empty dirs are skipped)
        assert len(result) >= 1, f"expected at least 1 run, got {len(result)}"

        for entry in result:
            assert "run_id" in entry, f"missing run_id in {entry}"
            assert "status" in entry, f"missing status in {entry}"
            assert "degraded" in entry, f"missing degraded in {entry}"
            assert "hydrated" in entry, f"missing hydrated in {entry}"
            assert entry["hydrated"] is False, "startup scan must set hydrated=False"
            assert entry["status"] in {"review", "error"}, (
                f"unexpected status {entry['status']!r} for run {entry['run_id']}"
            )

    def test_scan_cache_dirs_have_review_status(self) -> None:
        """Dirs with extraction_cache.json → status='review' (RH-002-S02)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        runs_dir = _real_runs_dir()
        if not runs_dir.is_dir():
            pytest.skip(f"backend/runs/ not found at {runs_dir}")

        # Find dirs that have extraction_cache.json (no manifest)
        dirs_with_cache = [
            d for d in runs_dir.iterdir()
            if d.is_dir()
            and (d / "extraction_cache.json").exists()
            and not (d / "run_manifest.json").exists()
        ]
        if not dirs_with_cache:
            pytest.skip("no legacy cache-only dirs found in backend/runs/")

        adapter = JsonManifestRunHistoryAdapter()
        result = adapter.scan(runs_dir)
        result_map = {e["run_id"]: e for e in result}

        for d in dirs_with_cache[:5]:  # check up to 5
            rid = d.name
            if rid in result_map:
                entry = result_map[rid]
                assert entry["status"] == "review", (
                    f"cache-only dir {rid} must have status='review', got {entry['status']!r}"
                )
                assert entry["degraded"] is True, f"cache-only dir {rid} must be degraded"

    def test_scan_pdf_only_dirs_have_error_status(self) -> None:
        """Dirs with only PDF → status='error' (RH-002)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        runs_dir = _real_runs_dir()
        if not runs_dir.is_dir():
            pytest.skip(f"backend/runs/ not found at {runs_dir}")

        # Find dirs that have only a PDF (no cache, no manifest)
        pdf_only_dirs = []
        for d in runs_dir.iterdir():
            if not d.is_dir():
                continue
            files = {f.name for f in d.iterdir()} if d.is_dir() else set()
            # PDF name matches UUID
            has_pdf = any(f.endswith(".pdf") for f in files)
            has_cache = "extraction_cache.json" in files
            has_manifest = "run_manifest.json" in files
            if has_pdf and not has_cache and not has_manifest:
                pdf_only_dirs.append(d)

        if not pdf_only_dirs:
            pytest.skip("no pdf-only dirs found in backend/runs/")

        adapter = JsonManifestRunHistoryAdapter()
        result = adapter.scan(runs_dir)
        result_map = {e["run_id"]: e for e in result}

        for d in pdf_only_dirs[:5]:
            rid = d.name
            if rid in result_map:
                entry = result_map[rid]
                assert entry["status"] == "error", (
                    f"pdf-only dir {rid} must have status='error', got {entry['status']!r}"
                )
                assert entry["degraded"] is True, f"pdf-only dir {rid} must be degraded"

    def test_corrupted_manifest_tolerance(self, tmp_path: Path) -> None:
        """Corrupted manifest (bad JSON) in a copy → skipped without exception.

        Uses a temp-dir copy — NEVER mutates real backend/runs/ dirs.
        """
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()

        # Copy: create a clean run (valid manifest) + a corrupt run
        valid_run_id = str(uuid.uuid4())
        corrupt_run_id = str(uuid.uuid4())

        valid_dir = tmp_path / valid_run_id
        valid_dir.mkdir()

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
        adapter.write_manifest(
            RunManifest(
                schema_version=1, run_id=valid_run_id, status="review",
                started_at="2026-06-11T00:00:00+00:00", completed_at=None,
                seq=1, registro_min=None, registro_max=None,
                row_count=0, match_count=0, mismatch_count=0,
                warnings=[], vision_calls_made=0,
            ),
            tmp_path,
        )

        corrupt_dir = tmp_path / corrupt_run_id
        corrupt_dir.mkdir()
        (corrupt_dir / "run_manifest.json").write_text("{CORRUPTED JSON", encoding="utf-8")

        # Must not raise
        result = adapter.scan(tmp_path)
        ids = {e["run_id"] for e in result}

        assert valid_run_id in ids, "valid run must appear"
        assert corrupt_run_id not in ids, "corrupted manifest must be skipped"
