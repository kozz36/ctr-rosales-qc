"""T1 / REV-R10 — VisionLLMPort.read_material_table Protocol contract.

Strict-TDD: tests written FIRST (RED) before implementation.
"""

from __future__ import annotations

import inspect

import pytest

from reconciliation.domain.models import MaterialLine, VisionResult
from reconciliation.domain.ports import VisionLLMPort


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


class _StubVisionWithTable:
    """Minimal stub satisfying VisionLLMPort INCLUDING read_material_table."""

    supports_batch: bool = False

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:
        return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:
        return []

    def read_material_table(
        self, image: bytes, hint: str | None = None
    ) -> list[MaterialLine]:
        return []


class _StubVisionWithoutTable:
    """Stub that does NOT implement read_material_table (should fail isinstance)."""

    supports_batch: bool = False

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:
        return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:
        return []


# ---------------------------------------------------------------------------
# T1 tests
# ---------------------------------------------------------------------------


class TestVisionLLMPortReadMaterialTable:
    def test_port_has_read_material_table_method(self) -> None:
        """VisionLLMPort Protocol must declare read_material_table."""
        assert hasattr(VisionLLMPort, "read_material_table"), (
            "VisionLLMPort must declare read_material_table method"
        )

    def test_stub_with_table_satisfies_protocol(self) -> None:
        """A stub implementing read_material_table satisfies VisionLLMPort."""
        stub = _StubVisionWithTable()
        assert isinstance(stub, VisionLLMPort)

    def test_stub_without_table_does_not_satisfy_protocol(self) -> None:
        """A stub missing read_material_table does NOT satisfy VisionLLMPort (RED gate).

        This test is the structural compliance gate — fails before the method
        is added to the Protocol.
        """
        stub = _StubVisionWithoutTable()
        assert not isinstance(stub, VisionLLMPort), (
            "A stub without read_material_table must NOT satisfy VisionLLMPort; "
            "add read_material_table to the Protocol first."
        )

    def test_null_adapter_satisfies_vision_port_after_implementation(self) -> None:
        """NullVisionAdapter must satisfy VisionLLMPort (fails until method added)."""
        from reconciliation.adapters.vision.null_vision import NullVisionAdapter

        adapter = NullVisionAdapter()
        assert isinstance(adapter, VisionLLMPort)
