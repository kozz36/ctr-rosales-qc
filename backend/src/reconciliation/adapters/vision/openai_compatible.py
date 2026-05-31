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


def _parse_vision_json(raw: str) -> VisionResult:
    """Defensively parse the model's JSON response.

    Handles:
    - markdown code fences (```json ... ```)
    - missing fields
    - invalid date strings
    - non-JSON text
    """
    try:
        clean = raw.strip()
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


def _build_messages(image: bytes, hint: str | None = None) -> list[dict[str, Any]]:
    """Construct the chat messages payload for a single image."""
    b64 = base64.b64encode(image).decode("ascii")
    user_text = "Extract the handwritten date from this image."
    if hint:
        user_text += f" Context hint: {hint}"
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
        max_tokens: Maximum output tokens.
        supports_batch: Whether to enable the batch path via OpenAI Batch
                        API.  Must be ``False`` for Ollama.
        client: Injected ``openai.OpenAI`` instance for testing.  When
                provided, the lazy-import path is skipped entirely.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        base_url: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 128,
        supports_batch: bool = True,
        client: object | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._max_tokens = max_tokens
        self.supports_batch = supports_batch
        self._client = client

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
            messages = _build_messages(image, hint)

            response = client.chat.completions.create(  # type: ignore[union-attr]
                model=self._model,
                max_tokens=self._max_tokens,
                messages=messages,  # type: ignore[arg-type]
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
        """Return the OpenAI client, building it lazily on first call."""
        if self._client is not None:
            return self._client

        from openai import OpenAI  # type: ignore[import]  # noqa: PLC0415

        kwargs: dict[str, Any] = {}
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
