"""Failing tests for VisionKeyStorePort contract.

Task 1.1 RED — VKS-002-S01/S02.
These tests assert the INTERFACE contract (Protocol) for VisionKeyStorePort
and VisionKeyProbePort. They will FAIL until application/vision_key_store.py
is created (Task 1.2 GREEN).
"""

from __future__ import annotations

import pytest


class TestVisionKeyStorePortContract:
    """VisionKeyStorePort Protocol contract (VKS-002-S01/S02)."""

    def test_port_protocol_is_importable(self) -> None:
        """VisionKeyStorePort can be imported from application layer."""
        from reconciliation.application.vision_key_store import VisionKeyStorePort  # noqa: PLC0415
        assert VisionKeyStorePort is not None

    def test_port_has_read_method(self) -> None:
        """VisionKeyStorePort defines read() -> str | None."""
        from reconciliation.application.vision_key_store import VisionKeyStorePort  # noqa: PLC0415
        import inspect  # noqa: PLC0415
        # Protocol must declare read
        assert "read" in dir(VisionKeyStorePort)
        # read() has no required parameters beyond self
        sig = inspect.signature(VisionKeyStorePort.read)  # type: ignore[attr-defined]
        params = [p for p in sig.parameters if p != "self"]
        assert params == [], f"read() should take no parameters beyond self, got {params}"

    def test_port_has_write_method(self) -> None:
        """VisionKeyStorePort defines write(key: str) -> None."""
        from reconciliation.application.vision_key_store import VisionKeyStorePort  # noqa: PLC0415
        import inspect  # noqa: PLC0415
        assert "write" in dir(VisionKeyStorePort)
        sig = inspect.signature(VisionKeyStorePort.write)  # type: ignore[attr-defined]
        params = [p for p in sig.parameters if p != "self"]
        assert "key" in params, f"write() should have 'key' param, got {params}"

    def test_concrete_impl_satisfies_read_missing_file(self, tmp_path: "Path") -> None:  # type: ignore[name-defined]
        """read() returns None when file is absent (VKS-002-S01)."""
        import os  # noqa: PLC0415
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        store = VisionKeyFileStore(secrets_dir=tmp_path / "no-such-dir")
        result = store.read()
        assert result is None

    def test_concrete_impl_roundtrip(self, tmp_path: "Path") -> None:  # type: ignore[name-defined]
        """write(key) + read() roundtrip returns the stored key (VKS-002-S02)."""
        from reconciliation.infrastructure.vision_key_file_store import VisionKeyFileStore  # noqa: PLC0415

        store = VisionKeyFileStore(secrets_dir=tmp_path / "secrets")
        store.write("test-api-key-12345")
        result = store.read()
        assert result == "test-api-key-12345"


class TestVisionKeyProbePortContract:
    """VisionKeyProbePort Protocol contract."""

    def test_probe_port_is_importable(self) -> None:
        """VisionKeyProbePort can be imported from application layer."""
        from reconciliation.application.vision_key_store import VisionKeyProbePort  # noqa: PLC0415
        assert VisionKeyProbePort is not None

    def test_key_probe_result_is_importable(self) -> None:
        """KeyProbeResult dataclass is importable from application layer."""
        from reconciliation.application.vision_key_store import KeyProbeResult  # noqa: PLC0415
        assert KeyProbeResult is not None

    def test_key_probe_result_fields(self) -> None:
        """KeyProbeResult has ok, reason, message fields."""
        from reconciliation.application.vision_key_store import KeyProbeResult  # noqa: PLC0415

        result = KeyProbeResult(ok=True, reason="valid", message="ok")
        assert result.ok is True
        assert result.reason == "valid"
        assert result.message == "ok"

    def test_key_probe_result_unauthorized(self) -> None:
        """KeyProbeResult can represent unauthorized state."""
        from reconciliation.application.vision_key_store import KeyProbeResult  # noqa: PLC0415

        result = KeyProbeResult(ok=False, reason="unauthorized", message="401")
        assert result.ok is False
        assert result.reason == "unauthorized"
