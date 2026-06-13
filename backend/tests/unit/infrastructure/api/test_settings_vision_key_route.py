"""Tests for POST /api/v1/settings/vision-key and DELETE /api/v1/settings/vision-key.

Task 4.2 RED → GREEN (after 4.3 implementation).
Covers VKS-001-S01 through S04.
JD-fix MEDIUM-4: DELETE off-ramp endpoint (VKS-006).

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

    def test_over_long_key_rejected_before_probe(self) -> None:
        """Key exceeding max_length=4096 → 422 Pydantic validation (never reaches probe).

        JD LOW-6: max_length=4096 guard on VisionKeySaveRequest.key.
        """
        client, store, probe = _make_client(
            KeyProbeResult(ok=True, reason="valid", message="ok")
        )

        over_long_key = "k" * 4097
        resp = client.post("/api/v1/settings/vision-key", json={"key": over_long_key})

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


class TestDeleteVisionKeyRoute:
    """DELETE /api/v1/settings/vision-key — off-ramp to kill vision (JD MEDIUM-4).

    JD RED→GREEN tests: DELETE endpoint clears the stored key via store.clear(),
    returns {restart_required: true}, and is idempotent (200 even when no key).
    """

    def _make_delete_client(
        self, has_key: bool = True
    ) -> tuple[TestClient, MagicMock, MagicMock]:
        """Build TestClient with key_store that has clear() and optional key present."""
        from reconciliation.infrastructure.run_history_store import (  # noqa: PLC0415
            JsonManifestRunHistoryAdapter,
        )

        probe = MagicMock()
        store = MagicMock()
        store.read.return_value = "existing-key" if has_key else None
        # clear() is the new method added by the fix
        store.clear = MagicMock(return_value=None)

        app = create_app()
        app.state.config = MagicMock()
        app.state.config.vision.enabled = False
        app.state.config.sunat.enabled = True
        app.state.run_registry = {}
        app.state.run_history = JsonManifestRunHistoryAdapter()
        app.state.key_store = store
        app.state.key_probe = probe

        client = TestClient(app, raise_server_exceptions=True)
        return client, store, probe

    def test_delete_returns_200_restart_required(self) -> None:
        """DELETE with existing key → 200 {restart_required: true} (VKS-006-S01).

        JD RED→GREEN test (MEDIUM-4): DELETE /settings/vision-key does not exist yet.
        """
        client, store, _ = self._make_delete_client(has_key=True)

        resp = client.delete("/api/v1/settings/vision-key")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("restart_required") is True

    def test_delete_calls_store_clear(self) -> None:
        """DELETE calls store.clear() to remove the key file (VKS-006-S01)."""
        client, store, _ = self._make_delete_client(has_key=True)

        client.delete("/api/v1/settings/vision-key")

        store.clear.assert_called_once()

    def test_delete_is_idempotent_when_no_key(self) -> None:
        """DELETE with no key file → 200 (idempotent, not 404) (VKS-006-S02)."""
        client, store, _ = self._make_delete_client(has_key=False)

        resp = client.delete("/api/v1/settings/vision-key")

        assert resp.status_code == 200
        store.clear.assert_called_once()

    def test_delete_returns_restart_required_true(self) -> None:
        """DELETE always returns restart_required=True (VKS-006-S01)."""
        client, store, _ = self._make_delete_client(has_key=False)

        resp = client.delete("/api/v1/settings/vision-key")

        data = resp.json()
        assert data.get("restart_required") is True
