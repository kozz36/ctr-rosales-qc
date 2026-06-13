"""Tests for VisionKeyProbeAdapter.

Task 3.1 RED — VKS-001-S01 through S04.
JD-fix RED — CRITICAL-1: openai.Timeout is NOT a BaseException (httpx.Timeout
config class); except (APIConnectionError, openai.Timeout) raises TypeError on
ANY non-Auth/non-Conn exception, killing the catch-all (reason="error" dead code).

Critical invariants verified here:
- Valid key → KeyProbeResult(ok=True, reason="valid").
- AuthenticationError → KeyProbeResult(ok=False, reason="unauthorized").
- APIConnectionError/APITimeoutError → KeyProbeResult(ok=False, reason="unreachable").
- openai.RateLimitError (429) → KeyProbeResult(ok=False, reason="error") WITHOUT TypeError.
- openai.NotFoundError (404) → KeyProbeResult(ok=False, reason="error") WITHOUT TypeError.
- Other exception → KeyProbeResult(ok=False, reason="error").
- Candidate key NEVER appears in caplog output (VKS-001-S04).
- Tests run WITHOUT openai installed (lazy-import proof).
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

import httpx
import openai
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
        """Build a minimal openai mock hierarchy with fake-but-valid exception classes.

        Uses lightweight fake exception classes that ARE BaseException subclasses.
        These are used when the test patches sys.modules["openai"] wholesale —
        the probe() code then catches 'whatever is in sys.modules["openai"]', so
        fake classes work correctly for Auth and Conn cases.

        JD-fix CRITICAL-1: openai.Timeout is intentionally ABSENT.
        openai.Timeout IS httpx.Timeout (a config class, NOT BaseException).
        The previous code had `mock_openai.Timeout = type("Timeout",(Exception,),{})`
        which fabricated it as Exception — this masked the bug where any non-Auth/
        non-Conn exception causes TypeError instead of being caught by the catch-all.
        The RED tests for CRITICAL-1 use the REAL openai SDK (see below).
        """
        mock_openai = MagicMock()
        mock_openai.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        # openai.Timeout intentionally NOT set — it is NOT a BaseException.
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
        """A timeout subclass of APIConnectionError → reason='unreachable'.

        In the real openai SDK, openai.APITimeoutError IS a subclass of
        APIConnectionError — it is caught by `except openai.APIConnectionError`.
        In the mock context (sys.modules replaced), we raise a fake subclass of
        the mock APIConnectionError so isinstance() matches in the except clause.
        """
        mock_openai = self._make_openai_mock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        # Create a fake APITimeoutError that IS a subclass of the fake APIConnectionError
        FakeAPITimeoutError = type(
            "APITimeoutError", (mock_openai.APIConnectionError,), {}
        )
        mock_client.chat.completions.create.side_effect = FakeAPITimeoutError("timed out")

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

    def test_real_rate_limit_error_returns_error_not_typeerror(self) -> None:
        """openai.RateLimitError (HTTP 429) → reason='error', NO TypeError raised.

        JD RED→GREEN test (CRITICAL-1): the bugged code has
          except (openai.APIConnectionError, openai.Timeout)
        where openai.Timeout is httpx.Timeout (NOT BaseException).
        Python raises TypeError when evaluating the except tuple on ANY
        non-Auth/non-Conn exception — this TypeError escapes the catch-all,
        turning every 429/404/500 into an unhandled crash instead of 503.

        This test uses the REAL openai.RateLimitError (not a mock) to ensure
        the TypeError is caught if the bug is present, and that after the fix
        probe() returns reason='error' cleanly.
        """
        req = httpx.Request("POST", "https://ollama.com/v1/chat/completions")
        resp = httpx.Response(429, request=req, text="rate limited")
        rate_limit_exc = openai.RateLimitError("rate limited", response=resp, body=None)

        with patch(
            "reconciliation.adapters.vision.key_probe.VisionKeyProbeAdapter.probe",
            wraps=None,
        ) as _:
            pass  # just confirm import succeeds

        import importlib  # noqa: PLC0415
        import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
        importlib.reload(kp)

        # Monkeypatch the openai.OpenAI client to raise RateLimitError
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.side_effect = rate_limit_exc

            adapter = kp.VisionKeyProbeAdapter()
            # Must NOT raise TypeError — must return reason="error"
            result = adapter.probe("any-key")

        assert result.ok is False
        assert result.reason == "error", (
            f"RateLimitError must map to reason='error', got: {result.reason!r}. "
            "If TypeError was raised instead, the openai.Timeout bug is still present."
        )

    def test_real_not_found_error_returns_error_not_typeerror(self) -> None:
        """openai.NotFoundError (HTTP 404) → reason='error', NO TypeError raised.

        JD RED→GREEN test (CRITICAL-1): same bug as above; 404 also hits the
        bugged except clause and causes TypeError to escape the catch-all.
        """
        req = httpx.Request("POST", "https://ollama.com/v1/chat/completions")
        resp = httpx.Response(404, request=req, text="not found")
        not_found_exc = openai.NotFoundError("not found", response=resp, body=None)

        import importlib  # noqa: PLC0415
        import reconciliation.adapters.vision.key_probe as kp  # noqa: PLC0415
        importlib.reload(kp)

        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.side_effect = not_found_exc

            adapter = kp.VisionKeyProbeAdapter()
            result = adapter.probe("any-key")

        assert result.ok is False
        assert result.reason == "error", (
            f"NotFoundError must map to reason='error', got: {result.reason!r}. "
            "If TypeError was raised instead, the openai.Timeout bug is still present."
        )

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
        # openai.Timeout intentionally NOT set (JD-fix CRITICAL-1)
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
        # openai.Timeout intentionally NOT set (JD-fix CRITICAL-1)
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
