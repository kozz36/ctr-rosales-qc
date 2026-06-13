"""Failing tests for VisionKeyProbeAdapter.

Task 3.1 RED — VKS-001-S01 through S04.
These tests will FAIL until adapters/vision/key_probe.py is created
(Task 3.2 GREEN).

Critical invariants verified here:
- Valid key → KeyProbeResult(ok=True, reason="valid").
- AuthenticationError → KeyProbeResult(ok=False, reason="unauthorized").
- APIConnectionError/timeout → KeyProbeResult(ok=False, reason="unreachable").
- Other exception → KeyProbeResult(ok=False, reason="error").
- Candidate key NEVER appears in caplog output (VKS-001-S04).
- Tests run WITHOUT openai installed (lazy-import proof).
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestVisionKeyProbeAdapterImport:
    """VisionKeyProbeAdapter is importable without openai installed."""

    def test_key_probe_importable_without_openai(self) -> None:
        """key_probe module imports without openai present (lazy-import proof)."""
        # Temporarily hide openai from sys.modules to simulate it being absent
        openai_mod = sys.modules.pop("openai", None)
        try:
            import importlib  # noqa: PLC0415
            # The module should import fine (no top-level openai import)
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)
            assert kp.VisionKeyProbeAdapter is not None
        finally:
            if openai_mod is not None:
                sys.modules["openai"] = openai_mod


class TestVisionKeyProbeAdapterProbe:
    """VisionKeyProbeAdapter.probe() outcome mapping (VKS-001-S01 to S04)."""

    def _make_openai_mock(self) -> MagicMock:
        """Build a minimal openai mock hierarchy."""
        mock_openai = MagicMock()
        # Set up exception classes that match the real openai structure
        mock_openai.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_openai.Timeout = type("Timeout", (Exception,), {})
        return mock_openai

    def test_valid_key_returns_ok_true(self) -> None:
        """Valid key (200) → KeyProbeResult(ok=True, reason='valid') (VKS-001-S01)."""
        mock_openai = self._make_openai_mock()
        # Simulate successful completion (no exception)
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"openai": mock_openai}):
            from reconciliation.adapters.vision.key_probe import VisionKeyProbeAdapter  # noqa: PLC0415
            import importlib  # noqa: PLC0415
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)

            adapter = kp.VisionKeyProbeAdapter()
            result = adapter.probe("valid-test-key")

        assert result.ok is True
        assert result.reason == "valid"

    def test_authentication_error_returns_unauthorized(self) -> None:
        """AuthenticationError (401) → KeyProbeResult(ok=False, reason='unauthorized') (VKS-001-S02)."""
        mock_openai = self._make_openai_mock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = mock_openai.AuthenticationError("401")

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib  # noqa: PLC0415
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)

            adapter = kp.VisionKeyProbeAdapter()
            result = adapter.probe("invalid-key-xyz")

        assert result.ok is False
        assert result.reason == "unauthorized"

    def test_connection_error_returns_unreachable(self) -> None:
        """APIConnectionError → KeyProbeResult(ok=False, reason='unreachable') (VKS-001-S03)."""
        mock_openai = self._make_openai_mock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = mock_openai.APIConnectionError("connection refused")

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib  # noqa: PLC0415
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)

            adapter = kp.VisionKeyProbeAdapter()
            result = adapter.probe("some-key")

        assert result.ok is False
        assert result.reason == "unreachable"

    def test_timeout_returns_unreachable(self) -> None:
        """openai.Timeout → KeyProbeResult(ok=False, reason='unreachable') (VKS-001-S03)."""
        mock_openai = self._make_openai_mock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = mock_openai.Timeout("timed out")

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib  # noqa: PLC0415
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)

            adapter = kp.VisionKeyProbeAdapter()
            result = adapter.probe("some-key")

        assert result.ok is False
        assert result.reason == "unreachable"

    def test_unexpected_exception_returns_error(self) -> None:
        """Unexpected exception → KeyProbeResult(ok=False, reason='error')."""
        mock_openai = self._make_openai_mock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("something unexpected")

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib  # noqa: PLC0415
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)

            adapter = kp.VisionKeyProbeAdapter()
            result = adapter.probe("some-key")

        assert result.ok is False
        assert result.reason == "error"

    def test_unauthorized_is_distinct_from_valid(self) -> None:
        """401 (unauthorized) is NOT ok=True — it is never treated as valid (VKS-001-S02)."""
        mock_openai = self._make_openai_mock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = mock_openai.AuthenticationError("401")

        with patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib  # noqa: PLC0415
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)

            adapter = kp.VisionKeyProbeAdapter()
            result = adapter.probe("definitely-invalid")

        assert result.ok is False, "401 must NOT be treated as valid"


class TestVisionKeyProbeNoKeyLogging:
    """VisionKeyProbeAdapter NEVER logs the candidate key value (VKS-001-S04)."""

    def test_key_not_in_caplog_on_valid(self, caplog: pytest.LogCaptureFixture) -> None:
        """Candidate key is absent from all log output on valid probe."""
        mock_openai = MagicMock()
        mock_openai.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_openai.Timeout = type("Timeout", (Exception,), {})
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock()

        secret_key = "super-secret-key-should-never-appear-in-logs-12345"

        with caplog.at_level(logging.DEBUG), patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib  # noqa: PLC0415
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)

            adapter = kp.VisionKeyProbeAdapter()
            adapter.probe(secret_key)

        all_log_text = " ".join(r.getMessage() for r in caplog.records)
        assert secret_key not in all_log_text, (
            f"Candidate key leaked into log output! Found in: {all_log_text!r}"
        )

    def test_key_not_in_caplog_on_auth_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Candidate key is absent from all log output on auth error."""
        mock_openai = MagicMock()
        mock_openai.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_openai.Timeout = type("Timeout", (Exception,), {})
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = mock_openai.AuthenticationError("401 unauthorized")

        secret_key = "another-secret-key-never-in-logs-abcdef"

        with caplog.at_level(logging.DEBUG), patch.dict("sys.modules", {"openai": mock_openai}):
            import importlib  # noqa: PLC0415
            import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
            importlib.reload(kp)

            adapter = kp.VisionKeyProbeAdapter()
            adapter.probe(secret_key)

        all_log_text = " ".join(r.getMessage() for r in caplog.records)
        assert secret_key not in all_log_text, (
            f"Candidate key leaked into log output on auth error! Found in: {all_log_text!r}"
        )
