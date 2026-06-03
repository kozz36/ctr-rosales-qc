"""OpenAICompatibleVisionAdapter — VisionLLMPort for OpenAI API and Ollama.

Uses the ``openai`` Python SDK with an optional ``base_url`` swap.  This
single adapter serves:

- **OpenAI cloud** — default ``base_url`` (``None``, let the SDK resolve),
  ``supports_batch=True`` (uses OpenAI Batch API for deferred processing).
- **Ollama** — ``base_url="http://localhost:11434/v1"``,
  ``supports_batch=False`` (Ollama does not support the Batch API).

The factory (``vision/factory.py``) sets these parameters based on
``AppConfig.vision.provider``.

**Lazy import**: ``openai`` is NOT imported at module top-level.

**JSON parsing**: model is prompted to return ONLY
``{"date": "YYYY-MM-DD"|null, "confidence": 0..1}``; parsing is defensive
(non-JSON → confidence=0, date=None).

**Error isolation**: any failure returns low-confidence VisionResult,
never raises from public methods.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import re
from datetime import date
from typing import Any

from reconciliation.domain.models import VisionResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a date-extraction specialist. "
    "The user will send you an image of a logistics document stamp. "
    "Extract the handwritten date if present. "
    'Respond with ONLY valid JSON: {"date": "YYYY-MM-DD", "confidence": 0.0-1.0} '
    'or {"date": null, "confidence": 0.0} if no date is visible. '
    "Do NOT include any other text or explanation."
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Matches <think>…</think> blocks emitted by extended-thinking models (e.g. qwen3.5).
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclasses.dataclass
class _TokenMeter:
    """Accumulates token consumption across all vision calls on this adapter instance.

    Invariant: all fields are additive — never reset between calls.
    Call ``record(usage)`` after each API response; read aggregate via ``total_tokens``.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def record(self, usage: object | None) -> None:
        """Accumulate token counts from an API usage object.

        Args:
            usage: OpenAI-compatible usage object with ``prompt_tokens`` and
                   ``completion_tokens`` attributes, or ``None`` (no-op).
        """
        if usage is None:
            return
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.calls += 1

    @property
    def total_tokens(self) -> int:
        """Sum of prompt and completion tokens across all recorded calls."""
        return self.prompt_tokens + self.completion_tokens


def _parse_vision_json(raw: str) -> VisionResult:
    """Defensively parse the model's JSON response.

    Handles:
    - ``<think>…</think>`` blocks from extended-thinking models (e.g. qwen3.5:9b).
      These blocks must be stripped BEFORE JSON parsing because they consume most of
      the token budget and are never part of the structured output.
    - markdown code fences (```json ... ```)
    - missing fields
    - invalid date strings
    - non-JSON text
    """
    try:
        # Strip think blocks first — qwen3.5 and similar thinking models prepend them.
        clean = _THINK_RE.sub("", raw).strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```[a-z]*\n?", "", clean)
            clean = re.sub(r"\n?```$", "", clean)
        data: dict[str, Any] = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        logger.debug("OpenAICompatibleVisionAdapter: non-JSON response: %r", raw[:200])
        return VisionResult(date=None, confidence=0.0, raw=raw)

    raw_date = data.get("date")
    confidence = float(data.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))

    parsed_date: date | None = None
    if isinstance(raw_date, str) and _DATE_RE.match(raw_date):
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError:
            pass

    return VisionResult(date=parsed_date, confidence=confidence, raw=raw)


def _build_messages(
    image: bytes,
    hint: str | None = None,
    disable_thinking: bool = False,
) -> list[dict[str, Any]]:
    """Construct the chat messages payload for a single image.

    Args:
        image: PNG bytes.
        hint: Optional context text appended to the user message.
        disable_thinking: When True, appends the literal token ``/no_think`` to
            the user message.  This is the Qwen convention for disabling the
            model's extended-thinking (<think>…</think>) phase via the
            OpenAI-compatible/Ollama path.  The token must appear in the user
            message text (not extra_body) because the Ollama chat-template
            processes it from the message content, not from extra parameters.
    """
    b64 = base64.b64encode(image).decode("ascii")
    user_text = "Extract the handwritten date from this image."
    if hint:
        user_text += f" Context hint: {hint}"
    if disable_thinking:
        user_text += " /no_think"
    return [
        {
            "role": "system",
            "content": _SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                },
            ],
        },
    ]


class OpenAICompatibleVisionAdapter:
    """Implements VisionLLMPort using the OpenAI-compatible chat completions API.

    Works with both OpenAI cloud and Ollama (or any OpenAI-compatible
    inference server) by swapping ``base_url``.

    Args:
        model: Model name to use (e.g. ``"gpt-4o"`` or ``"llava:latest"``).
        base_url: API base URL.  ``None`` uses the OpenAI SDK default
                  (``https://api.openai.com/v1``).  Set to
                  ``"http://localhost:11434/v1"`` for Ollama.
        api_key: API key string.  For Ollama, any non-empty string works.
        max_tokens: Maximum output tokens.  Default is 4096 because extended-thinking
                    models (e.g. qwen3.5:9b) consume the token budget with
                    ``<think>…</think>`` blocks before emitting structured output —
                    128 tokens is exhausted during the thinking phase, leaving an
                    empty ``content``.  Think-blocks are stripped by ``_parse_vision_json``
                    before JSON parsing.
        supports_batch: Whether to enable the batch path via OpenAI Batch
                        API.  Must be ``False`` for Ollama.
        timeout: Per-request timeout in seconds applied to both the OpenAI
                 client constructor (socket-level) and each
                 ``chat.completions.create()`` call (belt-and-suspenders so a
                 stalled read fails fast).  Default 30 s — normal no-think
                 calls complete in 2-3 s; 30 s bounds worst-case stalls without
                 retry amplification.
        disable_thinking: When True, appends ``/no_think`` to the user message
                          so Qwen3.5 skips its extended-thinking phase.  Safe
                          no-op for non-Qwen models.
        client: Injected ``openai.OpenAI`` instance for testing.  When
                provided, the lazy-import path is skipped entirely.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        base_url: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 4096,
        supports_batch: bool = True,
        timeout: float = 30.0,
        disable_thinking: bool = False,
        client: object | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._max_tokens = max_tokens
        self.supports_batch = supports_batch
        self._timeout = timeout
        self._disable_thinking = disable_thinking
        self._client = client
        # R10.6: per-instance token-consumption meter (CONT-S08)
        self._meter = _TokenMeter()

    @property
    def meter(self) -> _TokenMeter:
        """Read-only reference to the token-consumption meter for this adapter instance."""
        return self._meter

    # ------------------------------------------------------------------
    # VisionLLMPort interface
    # ------------------------------------------------------------------

    def read_handwritten_date(
        self,
        image: bytes,
        hint: str | None = None,
    ) -> VisionResult:
        """Extract a handwritten date from *image*.

        Args:
            image: PNG bytes of the date-stamp crop.
            hint:  Optional text context appended to the user message.

        Returns:
            VisionResult — always returns, never raises.
        """
        try:
            client = self._get_client()
            messages = _build_messages(image, hint, self._disable_thinking)

            response = client.chat.completions.create(  # type: ignore[union-attr]
                model=self._model,
                max_tokens=self._max_tokens,
                messages=messages,  # type: ignore[arg-type]
                timeout=self._timeout,
            )
            # R10.6: record token consumption from the response usage field (CONT-S08)
            self._meter.record(getattr(response, "usage", None))
            logger.debug(
                "vision meter: call=%d prompt=%d completion=%d total=%d (model=%s)",
                self._meter.calls,
                self._meter.prompt_tokens,
                self._meter.completion_tokens,
                self._meter.total_tokens,
                self._model,
            )
            raw: str = response.choices[0].message.content or ""  # type: ignore[index]
            return _parse_vision_json(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OpenAICompatibleVisionAdapter.read_handwritten_date failed: %s", exc
            )
            return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(
        self,
        images: list[bytes],
    ) -> list[VisionResult]:
        """Process *images* in batch (OpenAI Batch API) or sequentially (Ollama).

        When ``supports_batch=False`` (Ollama), falls back to sequential
        ``read_handwritten_date`` calls.

        Args:
            images: List of PNG bytes.

        Returns:
            List of VisionResult, same order as *images*.
        """
        if not images:
            return []

        if not self.supports_batch:
            return [self.read_handwritten_date(img) for img in images]

        try:
            return self._batch_via_openai_api(images)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OpenAICompatibleVisionAdapter.read_handwritten_date_batch failed, "
                "falling back to sequential: %s",
                exc,
            )
            return [self.read_handwritten_date(img) for img in images]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> object:
        """Return the OpenAI client, building it lazily on first call.

        ``timeout`` bounds the socket-level wait so a stalled ESTABLISHED
        connection fails after ``self._timeout`` seconds instead of waiting
        for the SDK default (~600 s).  ``max_retries=0`` disables SDK-level
        retries: a persistent stall must not multiply the timeout budget;
        degrade-and-continue (fecha=None, confidence=0) is the recovery path.
        """
        if self._client is not None:
            return self._client

        from openai import OpenAI  # type: ignore[import]  # noqa: PLC0415

        kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "max_retries": 0,
        }
        if self._api_key is not None:
            kwargs["api_key"] = self._api_key
        if self._base_url is not None:
            kwargs["base_url"] = self._base_url

        self._client = OpenAI(**kwargs)
        return self._client

    def _batch_via_openai_api(self, images: list[bytes]) -> list[VisionResult]:
        """Submit an OpenAI Batch API job and collect results.

        Creates a JSONL batch file, uploads it, submits the batch, polls
        until complete, and returns results in order.

        On any error, raises (caller will fall back to sequential).
        """
        import io as _io  # noqa: PLC0415
        import time  # noqa: PLC0415

        client = self._get_client()

        # Build JSONL batch file
        lines: list[str] = []
        for i, img in enumerate(images):
            messages = _build_messages(img)
            request_obj = {
                "custom_id": str(i),
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "messages": messages,
                },
            }
            lines.append(json.dumps(request_obj))

        jsonl_bytes = "\n".join(lines).encode("utf-8")
        jsonl_file = _io.BytesIO(jsonl_bytes)
        jsonl_file.name = "batch.jsonl"

        # Upload file
        uploaded = client.files.create(file=jsonl_file, purpose="batch")  # type: ignore[union-attr]

        # Create batch
        batch = client.batches.create(  # type: ignore[union-attr]
            input_file_id=uploaded.id,  # type: ignore[union-attr]
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        batch_id: str = batch.id  # type: ignore[union-attr]

        # Poll
        for _ in range(600):  # max ~10 min at 1s intervals
            status = client.batches.retrieve(batch_id)  # type: ignore[union-attr]
            if status.status in ("completed", "failed", "expired", "cancelled"):  # type: ignore[union-attr]
                break
            time.sleep(1)

        if status.status != "completed":  # type: ignore[union-attr]
            raise RuntimeError(f"OpenAI batch {batch_id} ended with status {status.status}")  # type: ignore[union-attr]

        # Retrieve output
        output_id: str = status.output_file_id  # type: ignore[union-attr]
        content_bytes = client.files.content(output_id).read()  # type: ignore[union-attr]

        results_map: dict[str, VisionResult] = {}
        for line in content_bytes.decode("utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj: dict[str, Any] = json.loads(line)
                cid = str(obj.get("custom_id", ""))
                body = obj.get("response", {}).get("body", {})
                choices = body.get("choices", [])
                if choices:
                    raw_text: str = choices[0].get("message", {}).get("content", "")
                    results_map[cid] = _parse_vision_json(raw_text)
                else:
                    results_map[cid] = VisionResult(date=None, confidence=0.0, raw="")
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        return [
            results_map.get(str(i), VisionResult(date=None, confidence=0.0, raw=""))
            for i in range(len(images))
        ]
