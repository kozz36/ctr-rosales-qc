"""VisionKeyProbeAdapter — dedicated key-validity probe (VKS-001).

Architecture:
  Implements VisionKeyProbePort (application/vision_key_store.py).
  Lives in adapters/vision/ — NEVER imported from domain/ or application/.

Design decision (D3):
  This is a THIN, DEDICATED probe — NOT a reuse of OpenAICompatibleVisionAdapter.
  OpenAICompatibleVisionAdapter's never-raise contract hides 401 errors (it
  returns low-confidence VisionResult on any failure), making it impossible to
  distinguish "unauthorized" from "benign empty read". VKS-001 requires that
  distinction explicitly.

Security invariants:
  - Candidate key NEVER logged (log only outcome + sanitized message).
  - openai is lazy-imported INSIDE probe() — never at module top.
  - Candidate key used per-call only (not stored on the instance).
  - message field in KeyProbeResult is sanitized (no key echo).

Defaults (baked in constructor — config, never a domain binding):
  base_url: https://ollama.com/v1
  model:    kimi-k2.5
  max_tokens: 1
  timeout:  10 seconds (short — probe only, not production inference)
"""

from __future__ import annotations

import logging

from reconciliation.application.vision_key_store import KeyProbeResult

logger = logging.getLogger(__name__)

_PROBE_BASE_URL = "https://ollama.com/v1"
_PROBE_MODEL = "kimi-k2.5"
_PROBE_MAX_TOKENS = 1
_PROBE_TIMEOUT = 10.0  # seconds


class VisionKeyProbeAdapter:
    """Concrete key-validity probe for Ollama Cloud (VKS-001).

    Args:
        base_url:   API base URL. Defaults to https://ollama.com/v1.
        model:      Model to use in the minimal probe call. Defaults to kimi-k2.5.
        max_tokens: Token budget for the probe call. Defaults to 1.
        timeout:    Request timeout in seconds. Defaults to 10.
    """

    def __init__(
        self,
        base_url: str = _PROBE_BASE_URL,
        model: str = _PROBE_MODEL,
        max_tokens: int = _PROBE_MAX_TOKENS,
        timeout: float = _PROBE_TIMEOUT,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    def probe(self, key: str) -> KeyProbeResult:
        """Probe whether *key* authenticates successfully against Ollama Cloud.

        Lazy-imports ``openai`` inside this method — never at module top.
        Candidate key used per-call only; NEVER stored or logged.

        Returns:
            KeyProbeResult(ok=True, reason="valid", ...) on success (200).
            KeyProbeResult(ok=False, reason="unauthorized", ...) on 401.
            KeyProbeResult(ok=False, reason="unreachable", ...) on connection/timeout.
            KeyProbeResult(ok=False, reason="error", ...) on unexpected failure.
        """
        # Lazy import — openai NOT at module top (hexagonal invariant).
        import openai  # noqa: PLC0415

        try:
            client = openai.OpenAI(
                api_key=key,
                base_url=self._base_url,
                timeout=self._timeout,
            )
            client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=self._max_tokens,
            )
            logger.info("vision key probe: valid (base_url=%s, model=%s)", self._base_url, self._model)
            return KeyProbeResult(ok=True, reason="valid", message="Key authenticated successfully.")

        except openai.AuthenticationError:
            # 401 — key is wrong / revoked.
            # Message is sanitized — no key value echoed.
            logger.warning(
                "vision key probe: unauthorized (base_url=%s, model=%s)",
                self._base_url,
                self._model,
            )
            return KeyProbeResult(
                ok=False,
                reason="unauthorized",
                message="API key was rejected (HTTP 401). Check that the key is valid.",
            )

        except openai.APIConnectionError:
            # Network/timeout — service unreachable.
            # openai.APITimeoutError is a subclass of APIConnectionError — covered.
            # openai.Timeout is httpx.Timeout (a config class, NOT BaseException) and
            # MUST NOT appear in an except clause — doing so raises TypeError at runtime
            # on any unrelated exception (JD CRITICAL-1).
            logger.warning(
                "vision key probe: unreachable (base_url=%s, model=%s)",
                self._base_url,
                self._model,
            )
            return KeyProbeResult(
                ok=False,
                reason="unreachable",
                message=(
                    f"Could not reach the vision API at {self._base_url}. "
                    "Check network connectivity and try again."
                ),
            )

        except Exception as exc:  # noqa: BLE001
            # Catch-all — unexpected failure; sanitize to avoid key leakage.
            # exc.__class__.__name__ is safe; str(exc) may contain key fragments
            # in some SDK versions so we avoid it.
            logger.warning(
                "vision key probe: unexpected error type=%s (base_url=%s)",
                type(exc).__name__,
                self._base_url,
            )
            return KeyProbeResult(
                ok=False,
                reason="error",
                message=f"Unexpected error during key probe ({type(exc).__name__}).",
            )
