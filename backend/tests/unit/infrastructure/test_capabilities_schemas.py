"""Failing tests for CapabilitiesResponse and VisionKeySaveRequest schemas.

Task 1.3 RED — CAP-001-S03, VKS-004.
These tests will FAIL until schemas.py is updated (Task 1.4 GREEN).
"""

from __future__ import annotations

import pytest


class TestCapabilitiesResponse:
    """CapabilitiesResponse schema has ONLY vision_enabled and sunat_enabled (CAP-001-S03)."""

    def test_capabilities_response_is_importable(self) -> None:
        """CapabilitiesResponse can be imported from schemas."""
        from reconciliation.infrastructure.api.schemas import CapabilitiesResponse  # noqa: PLC0415
        assert CapabilitiesResponse is not None

    def test_capabilities_response_has_exactly_two_fields(self) -> None:
        """CapabilitiesResponse has ONLY vision_enabled and sunat_enabled (CAP-001-S03)."""
        from reconciliation.infrastructure.api.schemas import CapabilitiesResponse  # noqa: PLC0415

        fields = set(CapabilitiesResponse.model_fields.keys())
        assert fields == {"vision_enabled", "sunat_enabled"}, (
            f"CapabilitiesResponse must have exactly 2 fields, got: {fields}"
        )

    def test_capabilities_response_no_api_key_field(self) -> None:
        """CapabilitiesResponse does NOT expose api_key, path, or model (CAP-001-S03)."""
        from reconciliation.infrastructure.api.schemas import CapabilitiesResponse  # noqa: PLC0415

        fields = set(CapabilitiesResponse.model_fields.keys())
        forbidden = {"api_key", "path", "model", "provider", "base_url"}
        leaked = fields & forbidden
        assert not leaked, f"CapabilitiesResponse must not expose: {leaked}"

    def test_capabilities_response_both_fields_are_bool(self) -> None:
        """vision_enabled and sunat_enabled are booleans."""
        from reconciliation.infrastructure.api.schemas import CapabilitiesResponse  # noqa: PLC0415

        resp = CapabilitiesResponse(vision_enabled=False, sunat_enabled=True)
        assert resp.vision_enabled is False
        assert resp.sunat_enabled is True

    def test_capabilities_response_serializes_correctly(self) -> None:
        """model_dump() returns only the two flag fields."""
        from reconciliation.infrastructure.api.schemas import CapabilitiesResponse  # noqa: PLC0415

        resp = CapabilitiesResponse(vision_enabled=True, sunat_enabled=False)
        data = resp.model_dump()
        assert data == {"vision_enabled": True, "sunat_enabled": False}


class TestVisionKeySaveRequest:
    """VisionKeySaveRequest validates the key field (min_length=1)."""

    def test_vision_key_save_request_is_importable(self) -> None:
        """VisionKeySaveRequest can be imported from schemas."""
        from reconciliation.infrastructure.api.schemas import VisionKeySaveRequest  # noqa: PLC0415
        assert VisionKeySaveRequest is not None

    def test_valid_key_accepted(self) -> None:
        """Non-empty key is accepted."""
        from reconciliation.infrastructure.api.schemas import VisionKeySaveRequest  # noqa: PLC0415

        req = VisionKeySaveRequest(key="some-api-key")
        assert req.key == "some-api-key"

    def test_empty_key_rejected(self) -> None:
        """Empty string key is rejected by min_length=1."""
        from pydantic import ValidationError  # noqa: PLC0415
        from reconciliation.infrastructure.api.schemas import VisionKeySaveRequest  # noqa: PLC0415

        with pytest.raises(ValidationError):
            VisionKeySaveRequest(key="")

    def test_missing_key_rejected(self) -> None:
        """Missing key field raises ValidationError."""
        from pydantic import ValidationError  # noqa: PLC0415
        from reconciliation.infrastructure.api.schemas import VisionKeySaveRequest  # noqa: PLC0415

        with pytest.raises(ValidationError):
            VisionKeySaveRequest()  # type: ignore[call-arg]


class TestVisionKeySaveResponse:
    """VisionKeySaveResponse carries restart_required flag."""

    def test_vision_key_save_response_is_importable(self) -> None:
        """VisionKeySaveResponse can be imported from schemas."""
        from reconciliation.infrastructure.api.schemas import VisionKeySaveResponse  # noqa: PLC0415
        assert VisionKeySaveResponse is not None

    def test_restart_required_field(self) -> None:
        """VisionKeySaveResponse has restart_required bool."""
        from reconciliation.infrastructure.api.schemas import VisionKeySaveResponse  # noqa: PLC0415

        resp = VisionKeySaveResponse(restart_required=True)
        assert resp.restart_required is True
