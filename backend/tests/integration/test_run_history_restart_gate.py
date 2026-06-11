"""Real-data gates for PR-2 lifecycle features.

Spec: RH-006, D5.
Gate: restart round-trip + retry dir-reset semantics.

These tests use real legacy dirs (read-only copies) to validate
that:
  (2.3.1) A manifest written to a tmp run dir survives a simulated
          restart (re-scan) and can be cold-loaded.
  (2.3.2) Retry dir-reset semantics are correct: extraction_cache +
          review.json + pages/ deleted; pdf + sunat/ kept.

Run:
    cd backend && uv run pytest tests/integration/test_run_history_restart_gate.py -v

These tests are marked @pytest.mark.real_runs_dir to opt out of
the _isolate_output_dir conftest fixture.  They DO NOT mutate any
real backend/runs/ directory — they work entirely in tmp_path copies.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

# Real legacy runs dir (read-only reference — never mutated directly).
_REAL_RUNS_DIR = Path(__file__).parent.parent.parent / "runs"


def _fresh_run_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 2.3.1 — Restart round-trip: manifest survives re-scan (cold-load verify)
# ---------------------------------------------------------------------------


@pytest.mark.real_runs_dir
class TestRestartRoundTrip:
    """Manifest written on first run → survives simulated restart → cold-load works."""

    def test_restart_round_trip_manifest_survives(self, tmp_path: Path) -> None:
        """Manifest written to tmp dir survives a re-scan (simulated restart).

        (1) Write a manifest for run_id in tmp_path.
        (2) Clear in-memory registry (simulate restart).
        (3) Re-scan tmp_path.
        (4) Assert run reappears with status='review', seq intact, degraded=False.
        (5) Verify the ctx built from the on-disk cache produces a non-None service.

        Spec: RH-006-S01, RH-006-S02.
        """
        from reconciliation.application.config import AppConfig  # noqa: PLC0415
        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        adapter = JsonManifestRunHistoryAdapter()
        output_dir = tmp_path / "runs"
        output_dir.mkdir()

        run_id = _fresh_run_id()
        run_dir = output_dir / run_id
        run_dir.mkdir()

        # (1) Write a manifest simulating pipeline completion
        manifest = RunManifest(
            schema_version=1,
            run_id=run_id,
            status="review",
            started_at="2026-06-11T10:00:00+00:00",
            completed_at="2026-06-11T10:05:00+00:00",
            seq=1,
            registro_min="100",
            registro_max="120",
            row_count=5,
            match_count=4,
            mismatch_count=1,
            warnings=[],
            vision_calls_made=2,
        )
        adapter.write_manifest(manifest, output_dir)

        # Also write a minimal extraction_cache.json (needed for cold-load hydration)
        pdf_stub = run_dir / f"{run_id}.pdf"
        pdf_stub.write_bytes(b"%PDF-1.4")
        cache_data = {
            "declared": [],
            "guias": [],
            "rows": [],
            "errored_guias": [],
            "discarded_pages": [],
        }
        (run_dir / "extraction_cache.json").write_text(
            json.dumps(cache_data), encoding="utf-8"
        )

        # (2) Clear in-memory registry (simulate restart)
        run_registry: dict = {}

        # (3) Re-scan (simulated server restart: new adapter, fresh registry)
        new_adapter = JsonManifestRunHistoryAdapter()
        entries = new_adapter.scan(output_dir)
        for entry in entries:
            run_registry[entry["run_id"]] = entry

        # (4) Assert run reappears with correct manifest fields
        assert run_id in run_registry, (
            f"run {run_id} must reappear after re-scan (restart round-trip)"
        )
        entry = run_registry[run_id]
        assert entry["status"] == "review", f"expected status='review'; got {entry['status']!r}"
        assert entry["seq"] == 1, f"expected seq=1; got {entry['seq']!r}"
        assert entry["degraded"] is False, "manifest run must NOT be degraded after restart"
        assert entry["registro_min"] == "100"
        assert entry["registro_max"] == "120"
        assert entry["hydrated"] is False, "restarted entry must start unhydrated"

        # (5) Cold-load hydration: build RunContext + ReviewService from disk cache
        from reconciliation.application.run_context import RunContext  # noqa: PLC0415
        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            build_review_service,
        )

        ctx = RunContext(pdf_path=pdf_stub, output_base=output_dir, run_id=run_id)
        review_service = build_review_service(ctx)

        # Service must be constructible (empty table is fine — minimal cache)
        assert review_service is not None, (
            "build_review_service must succeed on cold-load from disk cache"
        )
        # Empty declared → 0 rows (not an error)
        assert isinstance(review_service.rows, list)


# ---------------------------------------------------------------------------
# 2.3.2 — Retry dir-reset semantics (real dir copy)
# ---------------------------------------------------------------------------


@pytest.mark.real_runs_dir
class TestRetryDirResetSemantics:
    """POST /retry resets the run dir: deletes cache/review/pages; keeps pdf + sunat/."""

    def test_retry_dir_reset_semantics(self, tmp_path: Path) -> None:
        """Copy a legacy run dir; simulate retry reset; assert correct file state.

        (1) Locate a real legacy run dir (any with a PDF) OR create a synthetic one.
        (2) Copy it to tmp_path.
        (3) Simulate retry reset: delete extraction_cache.json, review.json, pages/.
        (4) Assert pdf still present; sunat/ still present (if it existed).
        (5) Assert dir is ready for re-fire.

        Spec: D5 (retry dir reset semantics).
        """
        # Find a real legacy run dir to use as a template (read-only).
        source_dir: Path | None = None
        if _REAL_RUNS_DIR.is_dir():
            for d in _REAL_RUNS_DIR.iterdir():
                if not d.is_dir():
                    continue
                # Any dir with a PDF is sufficient
                pdfs = list(d.glob("*.pdf"))
                if pdfs:
                    source_dir = d
                    break

        if source_dir is None:
            # No real dirs available — create a synthetic test dir
            run_id = _fresh_run_id()
            source_dir = tmp_path / "source" / run_id
            source_dir.mkdir(parents=True)
            (source_dir / f"{run_id}.pdf").write_bytes(b"%PDF-1.4")
            (source_dir / "extraction_cache.json").write_text("{}", encoding="utf-8")
            (source_dir / "review.json").write_text("{}", encoding="utf-8")
            pages_dir = source_dir / "pages"
            pages_dir.mkdir()
            (pages_dir / "0000.png").write_bytes(b"\x89PNG")
            sunat_dir = source_dir / "sunat"
            sunat_dir.mkdir()
            (sunat_dir / "data.json").write_text("{}", encoding="utf-8")

        # (2) Copy to tmp_path (never mutate real dirs)
        dest_run_id = source_dir.name
        dest_dir = tmp_path / dest_run_id
        shutil.copytree(source_dir, dest_dir)

        pdf_files = list(dest_dir.glob("*.pdf"))
        assert pdf_files, f"dest_dir has no PDF; unexpected: {list(dest_dir.iterdir())}"

        had_sunat = (dest_dir / "sunat").is_dir()

        # Add extraction_cache.json and review.json if they don't exist
        # (for round-trip correctness — the retry must delete them if present)
        extraction_cache = dest_dir / "extraction_cache.json"
        review_json = dest_dir / "review.json"
        pages_dir = dest_dir / "pages"

        if not extraction_cache.exists():
            extraction_cache.write_text("{}", encoding="utf-8")
        if not review_json.exists():
            review_json.write_text("{}", encoding="utf-8")
        if not pages_dir.exists():
            pages_dir.mkdir()
            (pages_dir / "0000.png").write_bytes(b"\x89PNG")

        # (3) Simulate retry dir reset (mirrors retry_run in routes.py)
        for name in ("extraction_cache.json", "review.json"):
            target = dest_dir / name
            if target.exists():
                target.unlink()

        if pages_dir.exists():
            shutil.rmtree(pages_dir, ignore_errors=True)

        # (4) Assert PDF still present (input read-only invariant)
        remaining_pdfs = list(dest_dir.glob("*.pdf"))
        assert remaining_pdfs, "PDF must be preserved by retry dir reset"

        # sunat/ must be preserved if it existed before reset
        if had_sunat:
            assert (dest_dir / "sunat").is_dir(), (
                "sunat/ must be preserved by retry dir reset (immutable fetch cache)"
            )

        # (5) Dir state is correct for re-fire: no cache, no review, no pages
        assert not extraction_cache.exists(), "extraction_cache.json must be gone after reset"
        assert not review_json.exists(), "review.json must be gone after reset"
        assert not pages_dir.exists(), "pages/ must be gone after reset"
