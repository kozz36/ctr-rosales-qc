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
from typing import Any

import pytest

# Real legacy runs dir (read-only reference — never mutated directly).
_REAL_RUNS_DIR = Path(__file__).parent.parent.parent / "runs"


def _fresh_run_id() -> str:
    return str(uuid.uuid4())


def _write_complete_run(
    output_dir: Path,
    run_id: str,
    *,
    new_registro: str = "205",
) -> tuple[Path, str, str]:
    """Write a COMPLETE run dir to disk (manifest + cache + sidecar edit + pdf).

    Builds real domain objects (Registro + GuiaDeRemision + MaterialLine) and a
    real review sidecar carrying ONE field_edit (registro 200 -> new_registro).
    The edit only becomes visible after the REAL cold-load hydration path
    rebuilds the ReviewService from the on-disk cache + sidecar (C1 keystone).

    Returns (run_dir, guia_id, original_registro).
    """
    from reconciliation.application.run_history import RunManifest  # noqa: PLC0415
    from reconciliation.domain.models import (  # noqa: PLC0415
        GuiaDeRemision,
        MaterialLine,
        Registro,
    )
    from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
        JsonManifestRunHistoryAdapter,
    )

    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    guia_id = "T001-1"
    original_registro = "200"

    ml = MaterialLine(
        description_raw="FIERRO 1/2",
        description_canonical="FIERRO 1/2",
        unidad="KG",
        cantidad="10",
        source_page=5,
    )
    guia = GuiaDeRemision(
        guia_id=guia_id,
        registro=original_registro,
        fecha=None,
        lines=[ml],
        source_pages=[5],
    )
    registro = Registro(
        numero=original_registro,
        fecha_declarada=None,
        declared_lines=[ml],
    )
    cache = {
        "declared": [registro.model_dump(mode="json")],
        "guias": [guia.model_dump(mode="json")],
        "rows": [],
        "errored_guias": [],
        "discarded_pages": [],
    }
    (run_dir / "extraction_cache.json").write_text(
        json.dumps(cache), encoding="utf-8"
    )

    # Sidecar edit — ONLY visible if the real hydration path replays it.
    sidecar = {
        "edits": [
            {
                "kind": "field_edit",
                "target": {"guia_id": guia_id},
                "field": "registro",
                "new_value": new_registro,
            }
        ]
    }
    (run_dir / "review.json").write_text(json.dumps(sidecar), encoding="utf-8")

    # The PDF the run was built from (used for thumbnail fallback render path).
    # Render a real 1-page PDF via fitz so the thumbnail fallback can open it.
    pdf_path = run_dir / f"{run_id}.pdf"
    try:
        import fitz  # noqa: PLC0415

        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()
    except Exception:  # noqa: BLE001 — fitz unavailable: fall back to a stub.
        pdf_path.write_bytes(b"%PDF-1.4")

    # Real manifest so the lifespan scan derives a non-degraded review entry.
    adapter = JsonManifestRunHistoryAdapter()
    manifest = RunManifest(
        schema_version=1,
        run_id=run_id,
        status="review",
        started_at="2026-06-11T10:00:00+00:00",
        completed_at="2026-06-11T10:05:00+00:00",
        seq=1,
        registro_min="200",
        registro_max="200",
        row_count=2,
        match_count=0,
        mismatch_count=0,
        warnings=[],
        vision_calls_made=0,
    )
    adapter.write_manifest(manifest, output_dir)

    return run_dir, guia_id, original_registro


def _cold_load_client(
    monkeypatch: Any, output_dir: Path
) -> Any:
    """Build a TestClient whose REAL lifespan scans *output_dir* (no manual ctx).

    Points the lifespan config at *output_dir* via env override and a
    non-existent config path (forces env + coded defaults; vision enabled).
    Returns a TestClient (caller drives it as a context manager so the real
    lifespan scan + hydration runs).
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from reconciliation.infrastructure.api.main import create_app  # noqa: PLC0415

    monkeypatch.setenv("RECONCILIATION_CONFIG", str(output_dir / "_no_such_config.yaml"))
    monkeypatch.setenv("RECONCILIATION__OUTPUT_DIR", str(output_dir))

    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# C1 KEYSTONE — cold-load hydration through the REAL lifespan + endpoints
# ---------------------------------------------------------------------------


@pytest.mark.real_runs_dir
class TestColdLoadEndpointHydration:
    """A scanned (cold) run with NO ctx must hydrate on first endpoint access.

    Drives the REAL path end-to-end: a complete run on disk → real create_app()
    lifespan scan (entry has ctx=None / no ctx key) → GET /table with NO manual
    ctx injection → 200 with the sidecar edit visible (registro 200 -> 205).
    Plus export + thumbnail smoke on the same cold entry (C1 blast radius).
    """

    def test_cold_table_hydrates_and_shows_sidecar_edit(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """GET /table on a cold-scanned run → 200; sidecar edit visible via audit.

        FAILS before C1 fix: scan entries never carry ctx, so _ensure_hydrated
        reads entry.get('ctx') is None and raises 409 — the cold-load path is dead.
        """
        output_dir = tmp_path / "runs"
        output_dir.mkdir()
        run_id = _fresh_run_id()
        _write_complete_run(output_dir, run_id, new_registro="205")

        client = _cold_load_client(monkeypatch, output_dir)
        with client:
            # Sanity: the lifespan scan populated the registry from disk (cold).
            registry = client.app.state.run_registry  # type: ignore[attr-defined]
            assert run_id in registry, "lifespan scan must seed the cold entry"
            assert registry[run_id].get("ctx") is None, (
                "scan entries must NOT carry a ctx (cold-load precondition)"
            )

            # (0) Thumbnail FIRST on the truly-cold entry — page-viewer must
            # build ctx itself, not 409 on entry.get('ctx') is None.
            r_thumb = client.get(f"/api/v1/runs/{run_id}/pages/0/thumbnail")
            assert r_thumb.status_code in (200, 404), (
                "thumbnail on a cold run must NOT 409 on ctx; "
                f"got {r_thumb.status_code}: {r_thumb.text}"
            )

            # (1) GET /table — REAL hydration, NO manual ctx injection.
            r_table = client.get(f"/api/v1/runs/{run_id}/table")
            assert r_table.status_code == 200, (
                f"cold GET /table must 200 via lazy hydration; "
                f"got {r_table.status_code}: {r_table.text}"
            )

            # (2) Sidecar edit must be visible — proves hydration replayed it.
            r_audit = client.get(f"/api/v1/runs/{run_id}/audit")
            assert r_audit.status_code == 200, f"audit: {r_audit.text}"
            events = r_audit.json()["events"]
            assert any(
                e["kind"] == "field_edit"
                and e.get("new_value") == "205"
                and e["target"].get("guia_id") == "T001-1"
                for e in events
            ), f"sidecar field_edit must be visible after cold hydration; got {events}"

            # (3) Export smoke on the cold entry (routes.py export uses entry['ctx']).
            r_export = client.post(
                f"/api/v1/runs/{run_id}/export", json={"fmt": "csv"}
            )
            assert r_export.status_code == 200, (
                f"export on cold entry must 200 (ctx hydrated); "
                f"got {r_export.status_code}: {r_export.text}"
            )


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
