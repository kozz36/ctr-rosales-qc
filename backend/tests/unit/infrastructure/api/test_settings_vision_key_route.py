"""Tests for POST /api/v1/settings/vision-key endpoint.

Task 4.2 RED → GREEN (after 4.3 implementation).
Covers VKS-001-S01 through S04.

Pattern: set app.state directly before TestClient creation, bypassing lifespan.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from reconciliation.application.vision_key_store import KeyProbeResult
from reconciliation.infrastructure.api.main import create_app


def _make_config() -> Any:
    cfg = MagicMock()
    cfg.vision.enabled = False
    cfg.sunat.enabled = True
    return cfg


def _make_client(probe_result: KeyProbeResult) -> tuple[TestClient, MagicMock, MagicMock]:
    """Build a TestClient with mocked probe + store on app.state.

    Returns (client, store_mock, probe_mock).
    """
    from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
        JsonManifestRunHistoryAdapter,
    )

    probe = MagicMock()
    probe.probe.return_value = probe_result

    store = MagicMock()
    store.read.return_value = None

    app = create_app()
    app.state.config = _make_config()
    app.state.run_registry = {}
    app.state.run_history = JsonManifestRunHistoryAdapter()
    app.state.key_store = store
    app.state.key_probe = probe

    client = TestClient(app, raise_server_exceptions=True)
    return client, store, probe


class TestSettingsVisionKeyRoute:
    """POST /api/v1/settings/vision-key — VKS-001."""

    def test_valid_key_returns_200_restart_required(self) -> None:
        """probe returns valid → 200 {'restart_required': true} + store.write called once (VKS-001-S01)."""
        client, store, probe = _make_client(
            KeyProbeResult(ok=True, reason="valid", message="ok")
        )

        resp = client.post("/api/v1/settings/vision-key", json={"key": "valid-key-abc"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["restart_required"] is True
        store.write.assert_called_once_with("valid-key-abc")

    def test_unauthorized_key_returns_400_no_write(self) -> None:
        """probe returns unauthorized → 400 + store.write NOT called (VKS-001-S02)."""
        client, store, probe = _make_client(
            KeyProbeResult(ok=False, reason="unauthorized", message="401")
        )

        resp = client.post("/api/v1/settings/vision-key", json={"key": "bad-key"})

        assert resp.status_code == 400
        store.write.assert_not_called()

    def test_unreachable_returns_503_no_write(self) -> None:
        """probe returns unreachable → 503 + store.write NOT called (VKS-001-S03)."""
        client, store, probe = _make_client(
            KeyProbeResult(ok=False, reason="unreachable", message="timeout")
        )

        resp = client.post("/api/v1/settings/vision-key", json={"key": "some-key"})

        assert resp.status_code == 503
        store.write.assert_not_called()

    def test_error_returns_503_no_write(self) -> None:
        """probe returns error → 503 + store.write NOT called."""
        client, store, probe = _make_client(
            KeyProbeResult(ok=False, reason="error", message="unexpected")
        )

        resp = client.post("/api/v1/settings/vision-key", json={"key": "some-key"})

        assert resp.status_code == 503
        store.write.assert_not_called()

    def test_empty_key_rejected_before_probe(self) -> None:
        """Empty key → 422 Pydantic validation (never reaches probe)."""
        client, store, probe = _make_client(
            KeyProbeResult(ok=True, reason="valid", message="ok")
        )

        resp = client.post("/api/v1/settings/vision-key", json={"key": ""})

        assert resp.status_code == 422
        probe.probe.assert_not_called()
        store.write.assert_not_called()

    def test_key_absent_from_log_on_valid(self, caplog: pytest.LogCaptureFixture) -> None:
        """Candidate key absent from all log lines on successful probe (VKS-001-S04)."""
        secret_key = "super-secret-route-level-key-never-log-9999"
        client, store, probe = _make_client(
            KeyProbeResult(ok=True, reason="valid", message="ok")
        )

        with caplog.at_level(logging.DEBUG):
            client.post("/api/v1/settings/vision-key", json={"key": secret_key})

        all_log_text = " ".join(r.getMessage() for r in caplog.records)
        assert secret_key not in all_log_text, (
            f"Key leaked into route log output: {all_log_text!r}"
        )
