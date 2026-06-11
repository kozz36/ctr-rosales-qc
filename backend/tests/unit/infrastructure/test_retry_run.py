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
# 2.1.12 — POST /retry 409 while another run is processing
# ---------------------------------------------------------------------------


class TestRetry409WhileProcessing:
    """POST /retry returns 409 when the retried run itself is processing."""

    def test_retry_409_while_processing(self, tmp_path: Path) -> None:
        """POST /retry on a run with status='processing' returns 409.

        FAILS before 2.2.3: endpoint doesn't exist.
        Spec: RH-007-S04.
        """
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()

        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "processing",
            "hydrated": False,
        }

        resp = client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 409, (
            f"retry while processing must return 409; got {resp.status_code}: {resp.text}"
        )
