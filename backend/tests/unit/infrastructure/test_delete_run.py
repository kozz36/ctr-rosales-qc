"""Unit tests for DELETE /runs/{run_id} endpoint.

Spec: RH-009, D5.
TDD Phase: RED — written before implementation.
"""

from __future__ import annotations

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


def _make_client(tmp_path: Path) -> TestClient:
    """TestClient with isolated state; bypasses real lifespan scan."""
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
# 2.1.4 — DELETE removes dir and registry entry (happy path)
# ---------------------------------------------------------------------------


class TestDeleteRemovesDirAndRegistry:
    """DELETE /runs/{run_id} removes the dir from disk and the entry from registry."""

    def test_delete_removes_dir_and_registry(self, tmp_path: Path) -> None:
        """DELETE /runs/{run_id} removes dir + registry, returns 204.

        FAILS before 2.2.2: the DELETE endpoint does not exist yet.
        """
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()

        # Create the run directory on disk
        run_dir = client.app.state.config.output_dir / run_id  # type: ignore[attr-defined]
        run_dir.mkdir(parents=True)
        (run_dir / f"{run_id}.pdf").write_bytes(b"%PDF-1.4")

        # Seed the registry entry
        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "review",
            "hydrated": False,
        }

        resp = client.delete(f"/api/v1/runs/{run_id}")

        assert resp.status_code == 204, f"expected 204, got {resp.status_code}: {resp.text}"
        assert not run_dir.exists(), f"run dir should be deleted; still exists: {run_dir}"
        assert run_id not in client.app.state.run_registry, (  # type: ignore[attr-defined]
            "registry entry must be removed after delete"
        )


# ---------------------------------------------------------------------------
# 2.1.5 — DELETE is scoped to own dir only
# ---------------------------------------------------------------------------


class TestDeleteScopedToOwnDir:
    """DELETE /runs/A does NOT delete run dir B."""

    def test_delete_scoped_to_own_dir(self, tmp_path: Path) -> None:
        """DELETE /runs/A leaves dir B intact (isolated delete).

        FAILS before 2.2.2: endpoint doesn't exist.
        """
        client = _make_client(tmp_path)
        run_a = _fresh_run_id()
        run_b = _fresh_run_id()

        config = client.app.state.config  # type: ignore[attr-defined]
        dir_a = config.output_dir / run_a
        dir_b = config.output_dir / run_b
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)

        client.app.state.run_registry[run_a] = {"run_id": run_a, "status": "review", "hydrated": False}  # type: ignore[attr-defined]
        client.app.state.run_registry[run_b] = {"run_id": run_b, "status": "review", "hydrated": False}  # type: ignore[attr-defined]

        resp = client.delete(f"/api/v1/runs/{run_a}")
        assert resp.status_code == 204, f"expected 204: {resp.text}"

        assert not dir_a.exists(), "dir A should be deleted"
        assert dir_b.exists(), "dir B must NOT be deleted"
        assert run_b in client.app.state.run_registry, "run B must remain in registry"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 2.1.6 — DELETE non-UUID path returns 400
# ---------------------------------------------------------------------------


class TestDeleteNonUuidReturns400:
    """DELETE /runs/{non-uuid} returns 400 before any filesystem call."""

    def test_delete_non_uuid_returns_400(self, tmp_path: Path) -> None:
        """DELETE with a non-UUID path segment returns 400 (CWE-22 guard).

        FAILS before 2.2.2: endpoint doesn't exist yet.
        """
        client = _make_client(tmp_path)

        resp = client.delete("/api/v1/runs/../../../etc/passwd")
        # FastAPI normalises path traversal; we test with a clearly non-UUID string
        assert resp.status_code in {400, 404}, (
            f"expected 400 for non-UUID run_id, got {resp.status_code}: {resp.text}"
        )

    def test_delete_short_id_returns_400(self, tmp_path: Path) -> None:
        """DELETE with a short non-UUID string returns 400."""
        client = _make_client(tmp_path)
        resp = client.delete("/api/v1/runs/not-a-uuid")
        assert resp.status_code == 400, (
            f"expected 400 for 'not-a-uuid', got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# 2.1.7 — DELETE unknown UUID returns 404
# ---------------------------------------------------------------------------


class TestDeleteUnknownReturns404:
    """DELETE /runs/{unknown_uuid} returns 404 when entry not in registry."""

    def test_delete_unknown_run_returns_404(self, tmp_path: Path) -> None:
        """DELETE /runs/{uuid} with empty registry returns 404.

        FAILS before 2.2.2: endpoint doesn't exist.
        """
        client = _make_client(tmp_path)
        unknown_id = _fresh_run_id()

        resp = client.delete(f"/api/v1/runs/{unknown_id}")
        assert resp.status_code == 404, (
            f"expected 404 for unknown run_id, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# 2.1.8 — DELETE processing run returns 409
# ---------------------------------------------------------------------------


class TestDeleteProcessingReturns409:
    """DELETE /runs/{run_id} returns 409 when run is actively processing."""

    def test_delete_processing_run_returns_409(self, tmp_path: Path) -> None:
        """DELETE while status='processing' raises 409 (cannot delete in-flight run).

        FAILS before 2.2.2: endpoint doesn't exist.
        """
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()

        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "processing",
            "hydrated": False,
        }

        resp = client.delete(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 409, (
            f"expected 409 for processing run, got {resp.status_code}: {resp.text}"
        )

    def test_delete_pending_run_returns_409(self, tmp_path: Path) -> None:
        """DELETE while status='pending' also raises 409."""
        client = _make_client(tmp_path)
        run_id = _fresh_run_id()

        client.app.state.run_registry[run_id] = {  # type: ignore[attr-defined]
            "run_id": run_id,
            "status": "pending",
            "hydrated": False,
        }

        resp = client.delete(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 409, (
            f"expected 409 for pending run, got {resp.status_code}: {resp.text}"
        )
