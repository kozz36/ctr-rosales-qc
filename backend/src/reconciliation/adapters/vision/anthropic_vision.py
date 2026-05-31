"""AnthropicVisionAdapter — VisionLLMPort backed by the Anthropic SDK.

Reads handwritten dates from guía page images using Claude's vision.

**Lazy import**: ``anthropic`` is NOT imported at module top-level.

**Batch support**: ``supports_batch = True``.  Uses the Anthropic
Message Batches API for the batch path, reducing latency and cost when
processing many pages in a single pipeline run.

**Error isolation**: any SDK / parsing failure returns a low-confidence
``VisionResult(date=None, confidence=0.0, raw="")`` — never raises from
public methods.  The pipeline treats confidence-0 results as missing dates.

**JSON parsing**: the model is prompted to return ONLY strict JSON:
``{"date": "YYYY-MM-DD" | null, "confidence": 0..1}``.  Parsing is
defensive: non-JSON responses, missing fields, and invalid dates all map
to ``confidence=0 / date=None``.
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
    """Defensively parse the model's JSON response into a VisionResult.

    On any error (non-JSON, missing fields, invalid date) returns
    ``VisionResult(date=None, confidence=0.0, raw=raw)``.
    """
    try:
        # Strip markdown fences if the model wraps the JSON anyway
        clean = raw.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```[a-z]*\n?", "", clean)
            clean = re.sub(r"\n?```$", "", clean)
        data: dict[str, Any] = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        logger.debug("AnthropicVisionAdapter: non-JSON response: %r", raw[:200])
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


class AnthropicVisionAdapter:
    """Implements VisionLLMPort using the Anthropic Messages API.

    Args:
        model: Claude model ID to use.
        max_tokens: Maximum tokens for the response.  Low values are fine
                    since we only expect a short JSON blob.
        client: Injected ``anthropic.Anthropic`` client.  When provided,
                the lazy-import path is skipped — use this in tests.
    """

    supports_batch: bool = True

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 128,
        client: object | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = client

    # ------------------------------------------------------------------
    # VisionLLMPort interface
    # ------------------------------------------------------------------

    def read_handwritten_date(
        self,
        image: bytes,
        hint: str | None = None,
    ) -> VisionResult:
        """Extract a handwritten date from *image* using Claude vision.

        Args:
            image: PNG bytes of the date-stamp crop.
            hint:  Optional text hint (e.g. nearby context) appended to the
                   system prompt.  Ignored if None.

        Returns:
            VisionResult — always returns, never raises.
        """
        try:
            client = self._get_client()
            b64 = base64.b64encode(image).decode("ascii")
            user_text = "Extract the handwritten date from this image."
            if hint:
                user_text += f" Context hint: {hint}"

            response = client.messages.create(  # type: ignore[union-attr]
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64,
                                },
                            },
                        ],
                    }
                ],
                system=_SYSTEM_PROMPT,
            )
            raw = response.content[0].text  # type: ignore[index]
            return _parse_vision_json(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AnthropicVisionAdapter.read_handwritten_date failed: %s", exc)
            return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(
        self,
        images: list[bytes],
    ) -> list[VisionResult]:
        """Submit *images* as a Message Batch and poll for results.

        Uses ``client.messages.batches.create`` (Anthropic Message Batches API).
        Falls back to sequential processing if batches fail.

        Args:
            images: List of PNG bytes, one per guía page.

        Returns:
            List of VisionResult in the same order as *images*.
        """
        if not images:
            return []

        try:
            client = self._get_client()
            requests = []
            for i, img in enumerate(images):
                b64 = base64.b64encode(img).decode("ascii")
                requests.append(
                    {
                        "custom_id": str(i),
                        "params": {
                            "model": self._model,
                            "max_tokens": self._max_tokens,
                            "system": _SYSTEM_PROMPT,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "Extract the handwritten date from this image.",
                                        },
                                        {
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": "image/png",
                                                "data": b64,
                                            },
                                        },
                                    ],
                                }
                            ],
                        },
                    }
                )

            batch = client.messages.batches.create(requests=requests)  # type: ignore[union-attr]
            return self._poll_batch(client, batch.id, len(images))  # type: ignore[union-attr]

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AnthropicVisionAdapter.read_handwritten_date_batch failed, "
                "falling back to sequential: %s",
                exc,
            )
            return [self.read_handwritten_date(img) for img in images]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> object:
        """Return the Anthropic client, importing the SDK lazily."""
        if self._client is not None:
            return self._client

        import anthropic  # type: ignore[import]  # noqa: PLC0415

        self._client = anthropic.Anthropic()
        return self._client

    def _poll_batch(
        self, client: object, batch_id: str, expected: int
    ) -> list[VisionResult]:
        """Poll until the batch is complete and collect results.

        Uses ``client.messages.batches.results(batch_id)`` iterator once
        the batch status is ``ended``.

        On any polling error, falls back to returning low-confidence results.
        """
        import time  # noqa: PLC0415

        try:
            # Poll until ended
            for _ in range(300):  # max ~5 min at 1s intervals
                batch = client.messages.batches.retrieve(batch_id)  # type: ignore[union-attr]
                if batch.processing_status == "ended":  # type: ignore[union-attr]
                    break
                time.sleep(1)

            # Collect results keyed by custom_id
            results_map: dict[str, VisionResult] = {}
            for result in client.messages.batches.results(batch_id):  # type: ignore[union-attr]
                cid: str = result.custom_id  # type: ignore[union-attr]
                if result.result.type == "succeeded":  # type: ignore[union-attr]
                    raw = result.result.message.content[0].text  # type: ignore[union-attr]
                    results_map[cid] = _parse_vision_json(raw)
                else:
                    results_map[cid] = VisionResult(date=None, confidence=0.0, raw="")

            return [
                results_map.get(str(i), VisionResult(date=None, confidence=0.0, raw=""))
                for i in range(expected)
            ]

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AnthropicVisionAdapter._poll_batch failed: %s", exc
            )
            return [VisionResult(date=None, confidence=0.0, raw="") for _ in range(expected)]
