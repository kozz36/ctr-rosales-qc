"""Unit tests for POST /runs/{run_id}/retry endpoint.

Spec: RH-007-S02, RH-007-S04, D5.
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


def _make_failed_run_dir(output_dir: Path, run_id: str) -> Path:
    """Create a failed run dir with pdf + cache + review.json + pages/."""
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True)
    (run_dir / f"{run_id}.pdf").write_bytes(b"%PDF-1.4")
    (run_dir / "extraction_cache.json").write_text("{}", encoding="utf-8")
    (run_dir / "review.json").write_text("{}", encoding="utf-8")
    pages_dir = run_dir / "pages"
    pages_dir.mkdir()
    (pages_dir / "0000.png").write_bytes(b"\x89PNG")
    sunat_dir = run_dir / "sunat"
    sunat_dir.mkdir()
    (sunat_dir / "some_cache.json").write_text("{}", encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# 2.1.9 — POST /retry reuses same run_id
# ---------------------------------------------------------------------------


class TestRetryReusesSameRunId:
    """POST /runs/{run_id}/retry re-fires pipeline with the SAME run_id."""

    def test_retry_reuses_same_run_id(self, tmp_path: Path) -> None:
        """POST /retry returns 202 with the original run_id (not a new UUID).

        FAILS before 2.2.3: the retry endpoint does not exist.
        """
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()
        config = client.app.state.config  # type: ignore[attr-defined]

        _make_failed_run_dir(config.output_dir, run_id)
        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "error",
            "error": "pipeline crashed",
            "hydrated": False,
            "pdf_path": str(config.output_dir / run_id / f"{run_id}.pdf"),
        }

        with patch(
            "reconciliation.infrastructure.api.routes._run_pipeline_background"
        ):
            resp = client.post(f"/api/v1/runs/{run_id}/retry")

        assert resp.status_code == 202, f"expected 202, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("run_id") == run_id, (
            f"retry must return the SAME run_id; got {data.get('run_id')!r}"
        )


# ---------------------------------------------------------------------------
# 2.1.10 — POST /retry 409 unless error status
# ---------------------------------------------------------------------------


class TestRetry409UnlessError:
    """POST /retry on a non-error run returns 409."""

    def test_retry_409_unless_error_status_review(self, tmp_path: Path) -> None:
        """POST /retry on a review (completed) run returns 409.

        FAILS before 2.2.3: endpoint doesn't exist.
        """
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()

        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "review",
            "hydrated": True,
        }

        resp = client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 409, (
            f"retry on completed run must return 409; got {resp.status_code}: {resp.text}"
        )

    def test_retry_404_for_unknown_run(self, tmp_path: Path) -> None:
        """POST /retry on unknown run_id returns 404."""
        client = _make_client(tmp_path)
        unknown = _fresh_run_id()

        resp = client.post(f"/api/v1/runs/{unknown}/retry")
        assert resp.status_code == 404, (
            f"expected 404 for unknown run; got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# 2.1.11 — POST /retry resets dir (deletes cache/review/pages), keeps pdf + sunat/
# ---------------------------------------------------------------------------


class TestRetryResetsDir:
    """POST /retry resets the run dir: removes cache/review/pages; keeps pdf + sunat/."""

    def test_retry_resets_dir_keeps_pdf_and_sunat(self, tmp_path: Path) -> None:
        """POST /retry deletes extraction_cache.json, review.json, pages/; keeps pdf + sunat/.

        FAILS before 2.2.3: retry endpoint doesn't exist.
        """
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()
        config = client.app.state.config  # type: ignore[attr-defined]

        run_dir = _make_failed_run_dir(config.output_dir, run_id)
        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "error",
            "error": "pipeline crashed",
            "hydrated": False,
            "pdf_path": str(run_dir / f"{run_id}.pdf"),
        }

        with patch(
            "reconciliation.infrastructure.api.routes._run_pipeline_background"
        ):
            resp = client.post(f"/api/v1/runs/{run_id}/retry")

        assert resp.status_code == 202, f"expected 202: {resp.text}"

        # Cache and review must be deleted
        assert not (run_dir / "extraction_cache.json").exists(), (
            "extraction_cache.json must be deleted by retry"
        )
        assert not (run_dir / "review.json").exists(), (
            "review.json must be deleted by retry"
        )
        # pages/ must be deleted
        assert not (run_dir / "pages").exists(), (
            "pages/ dir must be deleted by retry"
        )

        # PDF must be kept
        assert (run_dir / f"{run_id}.pdf").exists(), (
            "PDF must be preserved by retry (input is read-only invariant)"
        )

        # sunat/ must be kept
        assert (run_dir / "sunat").exists(), (
            "sunat/ dir must be preserved by retry (immutable fetch cache)"
        )


# ---------------------------------------------------------------------------
# 2.1.12a — POST /retry 409 when the retried run is itself in-flight (own status)
# ---------------------------------------------------------------------------


class TestRetry409OwnStatusInFlight:
    """POST /retry returns 409 when the retried run itself is processing/pending.

    This is the own-status guard (retry only valid on error runs), DISTINCT from
    the RH-007-S04 single-pipeline rule (another run busy).
    """

    def test_retry_409_own_status_processing(self, tmp_path: Path) -> None:
        """POST /retry on a run with status='processing' returns 409 (not error)."""
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()

        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "processing",
            "hydrated": False,
        }

        resp = client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 409, (
            f"retry on own in-flight run must return 409; got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# 2.1.12b — RH-007-S04: POST /retry rejected while ANOTHER run is processing
# ---------------------------------------------------------------------------


class TestRetry409WhileAnotherRunProcessing:
    """RH-007-S04: [Reintentar] while a DIFFERENT run is processing is rejected.

    The single-pipeline rule made explicit — never silently dropped or run
    concurrently with another in-flight run.
    """

    def test_retry_409_when_another_run_processing(self, tmp_path: Path) -> None:
        """A failed run + a DIFFERENT processing run → retry returns 409.

        FAILS before M-1: the route only checked the retried run's OWN status,
        so a failed run could be retried while another run was mid-pipeline,
        violating the single-pipeline rule (RH-007-S04).
        """
        client = _make_client(tmp_path)
        config = client.app.state.config  # type: ignore[attr-defined]

        failed_id = _fresh_run_id()
        other_id = _fresh_run_id()

        _make_failed_run_dir(config.output_dir, failed_id)
        registry = client.app.state.run_registry  # type: ignore[attr-defined]
        registry[failed_id] = {
            "run_id": failed_id,
            "status": "error",
            "error": "crashed",
            "hydrated": False,
            "pdf_path": str(config.output_dir / failed_id / f"{failed_id}.pdf"),
        }
        # A DIFFERENT run currently mid-pipeline.
        registry[other_id] = {
            "run_id": other_id,
            "status": "processing",
            "hydrated": False,
        }

        with patch(
            "reconciliation.infrastructure.api.routes._run_pipeline_background"
        ):
            resp = client.post(f"/api/v1/runs/{failed_id}/retry")

        assert resp.status_code == 409, (
            f"retry while another run processing must 409 (S04); "
            f"got {resp.status_code}: {resp.text}"
        )
        # And the failed run must NOT have been flipped to in-flight.
        assert registry[failed_id]["status"] == "error", (
            "rejected retry must leave the failed run's status untouched"
        )


# ---------------------------------------------------------------------------
# M-1 — concurrent double POST /retry → exactly ONE fires (TOCTOU guard)
# ---------------------------------------------------------------------------


class TestRetryDoubleFireGuard:
    """Two concurrent POST /retry on the same failed run → exactly one fires."""

    def test_concurrent_double_retry_fires_once(self, tmp_path: Path) -> None:
        """Two threads POST /retry simultaneously; _run_pipeline_background runs once.

        FAILS before M-1: sync endpoints run in a threadpool, so both requests
        pass the status==error check before either flips the status, and both
        schedule a background task (double-fire).
        """
        import threading  # noqa: PLC0415

        client = _make_client(tmp_path)
        config = client.app.state.config  # type: ignore[attr-defined]
        run_id = _fresh_run_id()

        _make_failed_run_dir(config.output_dir, run_id)
        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "error",
            "error": "crashed",
            "hydrated": False,
            "pdf_path": str(config.output_dir / run_id / f"{run_id}.pdf"),
        }

        fire_count = {"n": 0}
        fire_lock = threading.Lock()
        gate = threading.Barrier(2)

        def _counting_bg(*_a: Any, **_kw: Any) -> None:
            with fire_lock:
                fire_count["n"] += 1

        results: list[int] = []
        results_lock = threading.Lock()

        def _do_retry() -> None:
            gate.wait()  # maximise overlap on the check-and-set
            r = client.post(f"/api/v1/runs/{run_id}/retry")
            with results_lock:
                results.append(r.status_code)

        with patch(
            "reconciliation.infrastructure.api.routes._run_pipeline_background",
            side_effect=_counting_bg,
        ):
            t1 = threading.Thread(target=_do_retry)
            t2 = threading.Thread(target=_do_retry)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        assert fire_count["n"] == 1, (
            f"exactly ONE retry must fire under concurrency; fired {fire_count['n']} times"
        )
        assert sorted(results) == [202, 409], (
            f"one retry must 202 and the loser 409; got {sorted(results)}"
        )


# ---------------------------------------------------------------------------
# L-3 — same-day retry PRESERVES its original per-day seq (#N stable)
# ---------------------------------------------------------------------------


class TestRetryPreservesSeq:
    """A same-day retry completion keeps the run's original per-day seq."""

    def test_same_day_retry_keeps_seq(self, tmp_path: Path) -> None:
        """Retry a failed run whose manifest has seq=3 → completion manifest keeps seq=3.

        FAILS before L-3: write_manifest always re-allocates seq via
        _scan_max_seq+1, so a same-day retry would silently renumber the run.
        """
        import json  # noqa: PLC0415

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        client = _make_client(tmp_path)
        config = client.app.state.config  # type: ignore[attr-defined]
        run_id = _fresh_run_id()

        run_dir = _make_failed_run_dir(config.output_dir, run_id)

        # Prior manifest with a NON-1 seq (today's date so it's same-day).
        adapter = JsonManifestRunHistoryAdapter()
        started_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
        prior = RunManifest(
            schema_version=1,
            run_id=run_id,
            status="error",
            started_at=started_at,
            completed_at=started_at,
            seq=3,
            registro_min=None,
            registro_max=None,
            row_count=0,
            match_count=0,
            mismatch_count=0,
            warnings=[],
            vision_calls_made=0,
            error="crashed",
        )
        # Write with force_seq so the seed manifest keeps seq=3 exactly.
        adapter.write_manifest(prior, config.output_dir, force_seq=3)

        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "error",
            "error": "crashed",
            "hydrated": False,
            "pdf_path": str(run_dir / f"{run_id}.pdf"),
        }

        captured: dict[str, Any] = {}

        def _fake_bg(rid: str, *_a: Any, **_kw: Any) -> None:
            # Simulate the completion manifest write using the route's seq-preserve
            # path: read preserved_seq from the registry entry the route just set.
            entry = client.app.state.run_registry[rid]  # type: ignore[attr-defined]
            captured["preserved_seq"] = entry.get("preserved_seq")
            done = RunManifest(
                schema_version=1,
                run_id=rid,
                status="review",
                started_at=started_at,
                completed_at=started_at,
                seq=1,
                registro_min=None,
                registro_max=None,
                row_count=0,
                match_count=0,
                mismatch_count=0,
                warnings=[],
                vision_calls_made=0,
            )
            adapter.write_manifest(
                done, config.output_dir, force_seq=entry.get("preserved_seq")
            )

        with patch(
            "reconciliation.infrastructure.api.routes._run_pipeline_background",
            side_effect=_fake_bg,
        ):
            resp = client.post(f"/api/v1/runs/{run_id}/retry")

        assert resp.status_code == 202, f"retry: {resp.text}"
        assert captured.get("preserved_seq") == 3, (
            f"retry must thread the original seq (3) through; got {captured.get('preserved_seq')!r}"
        )

        # Completion manifest on disk must keep seq=3 (not renumbered).
        data = json.loads(
            (run_dir / "run_manifest.json").read_text(encoding="utf-8")
        )
        assert data["seq"] == 3, f"same-day retry must keep seq=3; got {data['seq']!r}"
        assert data["status"] == "review", "completion manifest must be review status"


# ---------------------------------------------------------------------------
# F-2(b) — belt, isolated: after POST /retry the ON-DISK manifest reads 'pending'
# ---------------------------------------------------------------------------


class TestRetryMarksManifestPendingOnDisk:
    """Endpoint-level proof of the H-1 'belt' guard, isolated from the suspenders.

    The belt (mark_pending → status='pending' on disk) and the suspenders
    (skip_run_ids passed to sweep_failed) mutually mask in the end-to-end flow.
    This test asserts the belt DIRECTLY on the persisted manifest immediately
    after retry (background pipeline stubbed so it never overwrites the manifest)
    — so it goes RED iff the mark_pending call in the retry endpoint is removed,
    independent of the sweep skip-set.
    """

    def test_retry_rewrites_disk_manifest_to_pending(self, tmp_path: Path) -> None:
        """POST /retry leaves the on-disk run_manifest.json at status='pending'.

        FAILS if the retry endpoint's mark_pending side-channel is disabled: the
        stale completion manifest would still read status='error' on disk.
        """
        import json  # noqa: PLC0415

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        client = _make_client(tmp_path)
        run_id = _fresh_run_id()
        config = client.app.state.config  # type: ignore[attr-defined]

        run_dir = _make_failed_run_dir(config.output_dir, run_id)
        # Seed a real error manifest on disk (the pre-retry persisted state).
        JsonManifestRunHistoryAdapter().write_manifest(
            RunManifest(
                schema_version=1, run_id=run_id, status="error",
                started_at="2026-06-11T00:00:00+00:00",
                completed_at="2026-06-11T00:01:00+00:00",
                seq=1, registro_min=None, registro_max=None,
                row_count=0, match_count=0, mismatch_count=0,
                warnings=[], vision_calls_made=0, error="boom",
            ),
            config.output_dir,
            force_seq=1,
        )

        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "error",
            "error": "boom",
            "hydrated": False,
            "pdf_path": str(run_dir / f"{run_id}.pdf"),
        }

        # Stub the background pipeline so it never overwrites the manifest — we
        # are asserting the BELT (mark_pending), not the completion write.
        with patch(
            "reconciliation.infrastructure.api.routes._run_pipeline_background"
        ):
            resp = client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 202, f"retry must accept: {resp.text}"

        disk = json.loads(
            (run_dir / "run_manifest.json").read_text(encoding="utf-8")
        )
        assert disk["status"] == "pending", (
            f"retry belt must rewrite on-disk manifest to 'pending'; got {disk['status']!r}"
        )
        assert disk.get("error") is None, "belt must clear the stale error string"
        assert disk["seq"] == 1, "belt must preserve the per-day seq identity"


# ---------------------------------------------------------------------------
# F-3 — cross-day retry must NOT preserve seq (else it duplicates today's #N)
# ---------------------------------------------------------------------------


class TestRetryCrossDaySeq:
    """seq preservation is a SAME-DAY identity rule. A run failed on day X and
    retried on day Y belongs to day Y's sequence; preserving X's seq would
    collide with day Y's existing #N (per-day numbering)."""

    def test_cross_day_retry_allocates_fresh_seq(self, tmp_path: Path) -> None:
        """A manifest started YESTERDAY retried today threads preserved_seq=None
        (fresh allocation), NOT the stale cross-day seq.

        FAILS before F-3: retry reads read_seq unconditionally, so a day-X seq-3
        run retried on day Y would carry seq=3 and duplicate day-Y's #3.
        """
        import datetime as _dt  # noqa: PLC0415
        import json  # noqa: PLC0415

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        client = _make_client(tmp_path)
        config = client.app.state.config  # type: ignore[attr-defined]
        run_id = _fresh_run_id()
        run_dir = _make_failed_run_dir(config.output_dir, run_id)

        adapter = JsonManifestRunHistoryAdapter()
        yesterday = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
        ).isoformat()
        prior = RunManifest(
            schema_version=1, run_id=run_id, status="error",
            started_at=yesterday, completed_at=yesterday,
            seq=3, registro_min=None, registro_max=None,
            row_count=0, match_count=0, mismatch_count=0,
            warnings=[], vision_calls_made=0, error="crashed",
        )
        adapter.write_manifest(prior, config.output_dir, force_seq=3)

        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "error",
            "error": "crashed",
            "hydrated": False,
            "pdf_path": str(run_dir / f"{run_id}.pdf"),
        }

        captured: dict[str, Any] = {}

        def _fake_bg(rid: str, *_a: Any, **_kw: Any) -> None:
            entry = client.app.state.run_registry[rid]  # type: ignore[attr-defined]
            captured["preserved_seq"] = entry.get("preserved_seq")

        with patch(
            "reconciliation.infrastructure.api.routes._run_pipeline_background",
            side_effect=_fake_bg,
        ):
            resp = client.post(f"/api/v1/runs/{run_id}/retry")

        assert resp.status_code == 202, f"retry: {resp.text}"
        assert captured.get("preserved_seq") is None, (
            "cross-day retry must NOT preserve the stale seq; "
            f"got preserved_seq={captured.get('preserved_seq')!r}"
        )


# ---------------------------------------------------------------------------
# F-4 — a retry that FAILS AGAIN keeps its seq (failure path honours force_seq)
# ---------------------------------------------------------------------------


class TestFailureManifestPreservesSeq:
    """write_failure_manifest must accept force_seq so a same-day failed retry
    keeps its #N. The background except-branch threads the preserved seq."""

    def test_failure_manifest_honours_force_seq(self, tmp_path: Path) -> None:
        """write_failure_manifest(force_seq=3) writes seq=3, not a re-allocation.

        FAILS before F-4: write_failure_manifest always allocates _scan_max_seq+1,
        so a failed retry renumbers the run.
        """
        import json  # noqa: PLC0415

        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)
        run_id = _fresh_run_id()
        (output_dir / run_id).mkdir()

        adapter.write_failure_manifest(
            run_id=run_id,
            started_at="2026-06-11T00:00:00+00:00",
            error_str="boom again",
            output_dir=output_dir,
            force_seq=3,
        )

        data = json.loads(
            (output_dir / run_id / "run_manifest.json").read_text(encoding="utf-8")
        )
        assert data["seq"] == 3, (
            f"failure manifest must honour force_seq=3; got {data['seq']!r}"
        )
        assert data["status"] == "error"
        assert data["error"] == "boom again"

    def test_failure_manifest_allocates_when_force_seq_none(self, tmp_path: Path) -> None:
        """force_seq=None keeps the original allocation behaviour (cross-day path).

        Guards against over-fitting: the new param must not break fresh allocation.
        """
        import json  # noqa: PLC0415

        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        output_dir = tmp_path / "runs"
        output_dir.mkdir(exist_ok=True)
        run_id = _fresh_run_id()
        (output_dir / run_id).mkdir()

        adapter.write_failure_manifest(
            run_id=run_id,
            started_at="2026-06-11T00:00:00+00:00",
            error_str="boom",
            output_dir=output_dir,
            force_seq=None,
        )

        data = json.loads(
            (output_dir / run_id / "run_manifest.json").read_text(encoding="utf-8")
        )
        assert data["seq"] == 1, f"fresh allocation must yield seq=1; got {data['seq']!r}"
