"""Unit tests for JsonManifestRunHistoryAdapter.

Spec: RH-001 (manifest write), RH-002 (scan + derive), RH-004 (seq), D2, D3, D4.
TDD Phase: RED — all tests FAIL before infrastructure/run_history_store.py exists.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_run_id() -> str:
    return str(uuid.uuid4())


def _make_manifest(
    run_id: str,
    started_at: str = "2026-06-11T00:00:00+00:00",
    status: str = "review",
    seq: int = 1,
) -> "RunManifest":  # type: ignore[name-defined]
    from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

    return RunManifest(
        schema_version=1,
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        completed_at="2026-06-11T00:01:00+00:00",
        seq=seq,
        registro_min="220",
        registro_max="245",
        row_count=10,
        match_count=8,
        mismatch_count=2,
        warnings=["w1"],
        vision_calls_made=3,
        error=None,
    )


# ---------------------------------------------------------------------------
# 1.1.4 — write_manifest creates valid JSON with schema_version
# ---------------------------------------------------------------------------


class TestWriteManifest:
    """Adapter write_manifest round-trip (RH-001-S01, D2)."""

    def test_write_manifest_creates_valid_json(self, tmp_path: Path) -> None:
        """write_manifest writes {run_id}/run_manifest.json with schema_version=1."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        run_id = _fresh_run_id()
        (tmp_path / run_id).mkdir()
        manifest = _make_manifest(run_id)

        adapter.write_manifest(manifest, tmp_path)

        manifest_path = tmp_path / run_id / "run_manifest.json"
        assert manifest_path.exists(), "manifest file must exist"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["run_id"] == run_id

    # 1.1.5 — atomic overwrite (NOT write-once)
    def test_write_manifest_is_atomic_overwrite_not_write_once(self, tmp_path: Path) -> None:
        """Second write_manifest call overwrites the first (retry semantics, D2)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        run_id = _fresh_run_id()
        (tmp_path / run_id).mkdir()

        m1 = _make_manifest(run_id)
        adapter.write_manifest(m1, tmp_path)

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

        m2 = RunManifest(
            schema_version=1,
            run_id=run_id,
            status="review",
            started_at="2026-06-11T01:00:00+00:00",
            completed_at="2026-06-11T01:05:00+00:00",
            seq=2,
            registro_min="300",
            registro_max="310",
            row_count=5,
            match_count=5,
            mismatch_count=0,
            warnings=[],
            vision_calls_made=0,
            error=None,
        )
        adapter.write_manifest(m2, tmp_path)  # must NOT raise

        data = json.loads((tmp_path / run_id / "run_manifest.json").read_text())
        assert data["started_at"] == "2026-06-11T01:00:00+00:00", "second write must overwrite"

    # 1.1.6 — IOError does not propagate (non-fatal, RH-001-S02)
    def test_write_manifest_ioerror_does_not_raise(self, tmp_path: Path) -> None:
        """OSError from write is caught; manifest failure MUST be non-fatal (RH-001-S02)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        run_id = _fresh_run_id()
        (tmp_path / run_id).mkdir()
        manifest = _make_manifest(run_id)

        with patch("reconciliation.infrastructure.run_history_store._atomic_json_write", side_effect=OSError("disk full")):
            adapter.write_manifest(manifest, tmp_path)  # must NOT raise

    # 1.1.7 — write_failure_manifest produces status=error (RH-001-S03)
    def test_write_failure_manifest_status_is_error(self, tmp_path: Path) -> None:
        """write_failure_manifest writes status='error', error=str, counts zero."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        run_id = _fresh_run_id()
        (tmp_path / run_id).mkdir()

        adapter.write_failure_manifest(
            run_id=run_id,
            started_at="2026-06-11T00:00:00+00:00",
            error_str="something went wrong",
            output_dir=tmp_path,
        )

        path = tmp_path / run_id / "run_manifest.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["status"] == "error"
        assert data["error"] == "something went wrong"
        assert data["registro_min"] is None
        assert data["registro_max"] is None
        assert data["row_count"] == 0


# ---------------------------------------------------------------------------
# 1.1.8/9 — Per-day seq allocation (D3, RH-004)
# ---------------------------------------------------------------------------


class TestSeqAllocation:
    """Per-day sequence numbers: write-time allocation under threading.Lock (D3)."""

    def test_seq_allocation_same_day_increments(self, tmp_path: Path) -> None:
        """Two manifests written same date get seq=1 and seq=2 (RH-004-S01, S02)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        date_prefix = "2026-06-11"

        run_a = _fresh_run_id()
        run_b = _fresh_run_id()
        (tmp_path / run_a).mkdir()
        (tmp_path / run_b).mkdir()

        ma = _make_manifest(run_a, started_at=f"{date_prefix}T00:00:00+00:00")
        adapter.write_manifest(ma, tmp_path)

        mb = _make_manifest(run_b, started_at=f"{date_prefix}T01:00:00+00:00")
        adapter.write_manifest(mb, tmp_path)

        data_a = json.loads((tmp_path / run_a / "run_manifest.json").read_text())
        data_b = json.loads((tmp_path / run_b / "run_manifest.json").read_text())
        seqs = {data_a["seq"], data_b["seq"]}
        assert seqs == {1, 2}, f"expected {{1, 2}} but got {seqs}"

    def test_seq_allocation_different_days_independent(self, tmp_path: Path) -> None:
        """Different date prefix → independent seq numbering (RH-004-S03)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()

        run_d1 = _fresh_run_id()
        run_d2 = _fresh_run_id()
        (tmp_path / run_d1).mkdir()
        (tmp_path / run_d2).mkdir()

        m_d1 = _make_manifest(run_d1, started_at="2026-06-11T00:00:00+00:00")
        adapter.write_manifest(m_d1, tmp_path)  # gets seq 1 for 2026-06-11

        m_d2 = _make_manifest(run_d2, started_at="2026-06-12T00:00:00+00:00")
        adapter.write_manifest(m_d2, tmp_path)  # must also get seq 1 for 2026-06-12

        data_d2 = json.loads((tmp_path / run_d2 / "run_manifest.json").read_text())
        assert data_d2["seq"] == 1, "first run on a new day must have seq=1"

    def test_seq_allocation_thread_safe(self, tmp_path: Path) -> None:
        """N concurrent same-day completions get unique seq 1–N (D3/RH-004).

        Deterministic: a threading.Barrier releases all writers at the exact
        same instant so they all race the scan→write critical section. Without
        the seq fix (scan under lock, write OUTSIDE), this maximises the TOCTOU
        window and collides reliably; with the lock held across scan AND write
        it is deterministically GREEN.
        """
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        date_prefix = "2026-06-11"
        n = 10
        run_ids = [_fresh_run_id() for _ in range(n)]
        for rid in run_ids:
            (tmp_path / rid).mkdir()

        errors: list[Exception] = []
        # Barrier-synchronise: all writers block until every thread is ready,
        # then enter the allocate→write path together (maximal contention).
        start_barrier = threading.Barrier(n)

        def write_one(rid: str) -> None:
            try:
                m = _make_manifest(rid, started_at=f"{date_prefix}T00:00:00+00:00")
                start_barrier.wait()
                adapter.write_manifest(m, tmp_path)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=write_one, args=(r,)) for r in run_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"threads raised: {errors}"
        seqs = set()
        for rid in run_ids:
            data = json.loads((tmp_path / rid / "run_manifest.json").read_text())
            seqs.add(data["seq"])

        assert seqs == set(range(1, n + 1)), f"expected 1–{n} unique seqs, got {seqs}"


# ---------------------------------------------------------------------------
# 1.1.11–16 — scan strategy (RH-002, D4)
# ---------------------------------------------------------------------------


class TestScan:
    """Scan output_dir: derive status from disk; legacy degrade gracefully."""

    def test_scan_completed_run_with_manifest(self, tmp_path: Path) -> None:
        """Scan returns full entry for a dir with a valid manifest (RH-002-S01, D4)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        run_id = _fresh_run_id()
        (tmp_path / run_id).mkdir()
        m = _make_manifest(run_id)
        adapter.write_manifest(m, tmp_path)

        entries = adapter.scan(tmp_path)
        ids = {e["run_id"] for e in entries}
        assert run_id in ids, "run with manifest must appear in scan"

        entry = next(e for e in entries if e["run_id"] == run_id)
        assert entry["status"] == "review"
        assert entry["hydrated"] is False  # never hydrated at startup
        assert entry["degraded"] is False

    def test_scan_legacy_run_extraction_cache_present(self, tmp_path: Path) -> None:
        """Legacy dir with extraction_cache.json → status='review', degraded=True (RH-002-S02)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        run_id = _fresh_run_id()
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        (run_dir / "extraction_cache.json").write_text("{}", encoding="utf-8")

        entries = adapter.scan(tmp_path)
        entry = next((e for e in entries if e["run_id"] == run_id), None)
        assert entry is not None
        assert entry["status"] == "review"
        assert entry["degraded"] is True

    def test_scan_legacy_run_pdf_only(self, tmp_path: Path) -> None:
        """Legacy dir with only a PDF → status='error', degraded=True (RH-002)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        run_id = _fresh_run_id()
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        (run_dir / f"{run_id}.pdf").write_bytes(b"%PDF-1.4")  # stub

        entries = adapter.scan(tmp_path)
        entry = next((e for e in entries if e["run_id"] == run_id), None)
        assert entry is not None
        assert entry["status"] == "error"
        assert entry["degraded"] is True

    def test_scan_corrupted_manifest_skipped(self, tmp_path: Path) -> None:
        """Corrupted JSON manifest → entry skipped (no exception); others present (RH-002-S03)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()

        # Valid manifest run
        run_valid = _fresh_run_id()
        (tmp_path / run_valid).mkdir()
        adapter.write_manifest(_make_manifest(run_valid), tmp_path)

        # Corrupted JSON
        run_corrupt = _fresh_run_id()
        run_corrupt_dir = tmp_path / run_corrupt
        run_corrupt_dir.mkdir()
        (run_corrupt_dir / "run_manifest.json").write_text("{NOT VALID JSON", encoding="utf-8")

        # Legacy (cache only) → should appear as degraded
        run_legacy = _fresh_run_id()
        run_legacy_dir = tmp_path / run_legacy
        run_legacy_dir.mkdir()
        (run_legacy_dir / "extraction_cache.json").write_text("{}", encoding="utf-8")

        entries = adapter.scan(tmp_path)
        ids = {e["run_id"] for e in entries}

        assert run_valid in ids, "valid run must appear"
        assert run_legacy in ids, "legacy run must appear as degraded"
        assert run_corrupt not in ids, "corrupted manifest must be skipped"

    def test_scan_empty_output_dir_returns_empty(self, tmp_path: Path) -> None:
        """scan on empty dir returns [] with no exception (RH-002-S04)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        result = adapter.scan(tmp_path)
        assert result == []

    def test_scan_non_uuid_dirs_ignored(self, tmp_path: Path) -> None:
        """Non-UUID subdirectory names are ignored; only UUID dirs scanned (D4)."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()

        # Non-UUID dir
        (tmp_path / "not-a-uuid").mkdir()

        # Valid UUID dir with manifest
        run_id = _fresh_run_id()
        (tmp_path / run_id).mkdir()
        adapter.write_manifest(_make_manifest(run_id), tmp_path)

        entries = adapter.scan(tmp_path)
        ids = {e["run_id"] for e in entries}
        assert run_id in ids
        assert "not-a-uuid" not in ids
