"""Tests for NullVisionAdapter — no-op VisionLLMPort for vision.enabled=False mode.

Verifies that the adapter:
  - Returns null-date VisionResult from read_handwritten_date.
  - Returns a list of null-date VisionResult (one per image) from read_handwritten_date_batch.
  - Never imports anthropic/openai SDK.
  - Structurally satisfies VisionLLMPort (runtime-checkable Protocol).
"""

from __future__ import annotations

import sys

import pytest

from reconciliation.adapters.vision.null_vision import NullVisionAdapter
from reconciliation.domain.models import VisionResult
from reconciliation.domain.ports import VisionLLMPort


class TestNullVisionAdapter:
    def test_read_handwritten_date_returns_null_vision_result(self) -> None:
        """read_handwritten_date returns VisionResult with date=None, confidence=0.0."""
        adapter = NullVisionAdapter()
        result = adapter.read_handwritten_date(b"\x89PNG\r\n")
        assert isinstance(result, VisionResult)
        assert result.date is None
        assert result.confidence == 0.0

    def test_read_handwritten_date_raw_is_empty_string(self) -> None:
        """read_handwritten_date returns raw='' (no raw text from null adapter)."""
        adapter = NullVisionAdapter()
        result = adapter.read_handwritten_date(b"\x89PNG\r\n")
        assert result.raw == ""

    def test_read_handwritten_date_hint_ignored(self) -> None:
        """hint parameter is accepted but ignored — always returns null result."""
        adapter = NullVisionAdapter()
        result = adapter.read_handwritten_date(b"\x89PNG\r\n", hint="28/05/2026")
        assert result.date is None
        assert result.confidence == 0.0

    def test_read_handwritten_date_batch_returns_one_result_per_image(self) -> None:
        """read_handwritten_date_batch returns one null VisionResult per input image."""
        adapter = NullVisionAdapter()
        images = [b"\x89PNG", b"\x89PNG", b"\x89PNG"]
        results = adapter.read_handwritten_date_batch(images)
        assert len(results) == len(images)

    def test_read_handwritten_date_batch_all_null_date(self) -> None:
        """All batch results have date=None."""
        adapter = NullVisionAdapter()
        results = adapter.read_handwritten_date_batch([b"\x89PNG", b"\x89PNG"])
        for r in results:
            assert isinstance(r, VisionResult)
            assert r.date is None
            assert r.confidence == 0.0
            assert r.raw == ""

    def test_read_handwritten_date_batch_empty_input_returns_empty(self) -> None:
        """read_handwritten_date_batch([]) returns []."""
        adapter = NullVisionAdapter()
        assert adapter.read_handwritten_date_batch([]) == []

    def test_supports_batch_is_bool(self) -> None:
        """supports_batch is a bool attribute on the adapter."""
        adapter = NullVisionAdapter()
        assert isinstance(adapter.supports_batch, bool)

    def test_satisfies_vision_llm_port_protocol(self) -> None:
        """NullVisionAdapter must satisfy VisionLLMPort runtime-checkable protocol."""
        adapter = NullVisionAdapter()
        assert isinstance(adapter, VisionLLMPort)

    def test_no_sdk_imported_after_construction_and_calls(self) -> None:
        """Constructing and calling NullVisionAdapter must not import anthropic/openai SDK.

        Guards the invariant that vision.enabled=False never initialises LLM clients.
        """
        sdk_keys_before = {
            k for k in sys.modules if k.startswith("anthropic") or k.startswith("openai")
        }

        adapter = NullVisionAdapter()
        adapter.read_handwritten_date(b"\x89PNG")
        adapter.read_handwritten_date_batch([b"\x89PNG"])

        sdk_keys_after = {
            k for k in sys.modules if k.startswith("anthropic") or k.startswith("openai")
        }
        newly_imported = sdk_keys_after - sdk_keys_before
        assert newly_imported == set(), (
            f"NullVisionAdapter imported SDK modules: {newly_imported}"
        )
