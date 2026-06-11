"""Unit tests for RunManifest schema and RunHistoryPort Protocol.

Spec: RH-001 (manifest written at pipeline completion), D2 (schema).
TDD Phase: RED — all tests FAIL before application/run_history.py exists.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 1.1.1 — RunManifest schema_version=1 baseline
# ---------------------------------------------------------------------------


class TestRunManifestSchema:
    """Verify RunManifest Pydantic model fields and constraints (D2)."""

    def test_run_manifest_schema_version_is_1(self) -> None:
        """RunManifest instantiates with schema_version=1 (D2)."""
        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

        m = RunManifest(
            schema_version=1,
            run_id="abc123",
            status="review",
            started_at="2026-06-11T00:00:00+00:00",
            completed_at=None,
            seq=1,
            registro_min=None,
            registro_max=None,
            row_count=0,
            match_count=0,
            mismatch_count=0,
            warnings=[],
            vision_calls_made=0,
            error=None,
        )
        assert m.schema_version == 1

    # 1.1.2 — no pdf_filename field (CWE-22 invariant, RH-001-S04)
    def test_run_manifest_no_pdf_filename_field(self) -> None:
        """RunManifest MUST NOT have a pdf_filename or filename field (CWE-22)."""
        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

        field_names = set(RunManifest.model_fields.keys())
        assert "pdf_filename" not in field_names, "CWE-22: pdf_filename must not exist"
        assert "filename" not in field_names, "CWE-22: filename must not exist"

    # 1.1.3 — status Literal validation (D2)
    def test_run_manifest_status_values(self) -> None:
        """RunManifest accepts 'review' and 'error' but not arbitrary strings (D2)."""
        from pydantic import ValidationError  # noqa: PLC0415

        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

        def _make(status: str) -> RunManifest:
            return RunManifest(
                schema_version=1,
                run_id="abc",
                status=status,  # type: ignore[arg-type]
                started_at="2026-06-11T00:00:00+00:00",
                completed_at=None,
                seq=1,
                registro_min=None,
                registro_max=None,
                row_count=0,
                match_count=0,
                mismatch_count=0,
                warnings=[],
                vision_calls_made=0,
            )

        # Valid statuses
        r = _make("review")
        assert r.status == "review"
        e = _make("error")
        assert e.status == "error"

        # Invalid status should raise
        with pytest.raises(ValidationError):
            _make("pending")

        with pytest.raises(ValidationError):
            _make("arbitrary_garbage")


# ---------------------------------------------------------------------------
# 1.1.x — RunHistoryPort Protocol structural check
# ---------------------------------------------------------------------------


class TestRunHistoryPort:
    """RunHistoryPort is a typing Protocol (pure, no IO)."""

    def test_run_history_port_is_importable(self) -> None:
        """RunHistoryPort can be imported from application layer."""
        from reconciliation.application.run_history import RunHistoryPort  # noqa: PLC0415

        assert RunHistoryPort is not None

    def test_run_history_port_has_required_methods(self) -> None:
        """RunHistoryPort Protocol defines all required method signatures."""
        import inspect  # noqa: PLC0415

        from reconciliation.application.run_history import RunHistoryPort  # noqa: PLC0415

        members = {name for name, _ in inspect.getmembers(RunHistoryPort)}
        assert "write_manifest" in members
        assert "write_failure_manifest" in members
        assert "scan" in members
        assert "sweep_failed" in members
        assert "delete_run" in members
