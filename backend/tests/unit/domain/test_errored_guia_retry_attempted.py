"""Tests for ErroredGuia.retry_attempted field (REV-E01).

TDD RED phase — these tests MUST fail until T-1 adds retry_attempted to ErroredGuia.

Covers:
  - retry_attempted defaults to False on construction
  - model_dump(mode="json") serialises the field
  - old cache dict WITHOUT the field loads gracefully (backward-compat)
"""

from __future__ import annotations

from reconciliation.domain.models import ErroredGuia


class TestErroredGuiaRetryAttempted:
    def test_defaults_false(self) -> None:
        """retry_attempted is False when not supplied."""
        eg = ErroredGuia(registro="R001", guia_id="T009-0001", source_pages=[5])
        assert eg.retry_attempted is False

    def test_can_be_set_true(self) -> None:
        """retry_attempted can be explicitly set to True."""
        eg = ErroredGuia(
            registro="R001",
            guia_id="T009-0001",
            source_pages=[5],
            retry_attempted=True,
        )
        assert eg.retry_attempted is True

    def test_model_dump_includes_field(self) -> None:
        """model_dump(mode='json') serialises retry_attempted as False."""
        eg = ErroredGuia(registro="R002", guia_id="T009-0002", source_pages=[3, 4])
        dumped = eg.model_dump(mode="json")
        assert "retry_attempted" in dumped
        assert dumped["retry_attempted"] is False

    def test_model_dump_existing_keys_unaltered(self) -> None:
        """Existing keys registro/guia_id/source_pages are present and correct."""
        eg = ErroredGuia(registro="R003", guia_id="T009-0003", source_pages=[7])
        dumped = eg.model_dump(mode="json")
        assert dumped["registro"] == "R003"
        assert dumped["guia_id"] == "T009-0003"
        assert dumped["source_pages"] == [7]

    def test_backward_compat_model_validate_without_field(self) -> None:
        """Old cache dict WITHOUT retry_attempted loads without error, defaults False."""
        old_cache_dict = {
            "registro": "R100",
            "guia_id": "T001-0001",
            "source_pages": [1, 2],
            # retry_attempted intentionally absent
        }
        eg = ErroredGuia.model_validate(old_cache_dict)
        assert eg.registro == "R100"
        assert eg.guia_id == "T001-0001"
        assert eg.source_pages == [1, 2]
        assert eg.retry_attempted is False

    def test_backward_compat_round_trip(self) -> None:
        """Dumped dict from new code re-validates cleanly."""
        eg1 = ErroredGuia(registro="R005", guia_id="T009-0005", source_pages=[10])
        eg2 = ErroredGuia.model_validate(eg1.model_dump(mode="json"))
        assert eg2.retry_attempted is False
        assert eg2.guia_id == "T009-0005"
