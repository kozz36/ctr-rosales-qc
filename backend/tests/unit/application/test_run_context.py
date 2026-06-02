"""Tests for RunContext — per-run I/O isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reconciliation.application.run_context import RunContext


class TestRunContextCreation:
    def test_creates_run_dir(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        assert ctx.run_dir.exists()

    def test_run_id_is_uuid_like(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        # UUID4 has 36 chars with dashes
        assert len(ctx.run_id) == 36

    def test_explicit_run_id(self, tmp_path: Path) -> None:
        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
            run_id="my-run-001",
        )
        assert ctx.run_id == "my-run-001"
        assert ctx.run_dir == tmp_path / "runs" / "my-run-001"

    def test_pdf_path_is_read_only_reference(self, tmp_path: Path) -> None:
        pdf = tmp_path / "input.pdf"
        ctx = RunContext(pdf_path=pdf, output_base=tmp_path / "runs")
        assert ctx.pdf_path == pdf

    def test_extraction_cache_path_under_run_dir(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        assert ctx.extraction_cache.parent == ctx.run_dir
        assert ctx.extraction_cache.name == "extraction_cache.json"

    def test_review_sidecar_path_under_run_dir(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        assert ctx.review_sidecar.parent == ctx.run_dir
        assert ctx.review_sidecar.name == "review.json"


class TestExtractionCache:
    def test_no_cache_initially(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        assert not ctx.has_extraction_cache()

    def test_write_and_read_cache(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        data = {"pages": 5, "guias": ["g1", "g2"]}
        ctx.write_extraction_cache(data)

        assert ctx.has_extraction_cache()
        loaded = ctx.read_extraction_cache()
        assert loaded == data

    def test_write_once_immutable(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        ctx.write_extraction_cache({"first": True})
        with pytest.raises(RuntimeError, match="immutable"):
            ctx.write_extraction_cache({"second": True})

    def test_read_missing_cache_raises(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        with pytest.raises(FileNotFoundError):
            ctx.read_extraction_cache()

    def test_cache_is_valid_json(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        ctx.write_extraction_cache({"key": "value", "num": 42})
        raw = ctx.extraction_cache.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["key"] == "value"


class TestReviewSidecar:
    def test_no_sidecar_initially(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        assert not ctx.has_review_sidecar()

    def test_read_empty_sidecar_returns_empty_dict(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        assert ctx.read_review_sidecar() == {}

    def test_write_and_read_sidecar(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        data = {"edits": [{"kind": "field_edit", "guia_id": "g1"}]}
        ctx.write_review_sidecar(data)
        loaded = ctx.read_review_sidecar()
        assert loaded == data

    def test_sidecar_is_overwritable(self, tmp_path: Path) -> None:
        """Unlike the extraction cache, the sidecar can be overwritten."""
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        ctx.write_review_sidecar({"edits": ["first"]})
        ctx.write_review_sidecar({"edits": ["second"]})
        loaded = ctx.read_review_sidecar()
        assert loaded["edits"] == ["second"]

    def test_atomic_write_leaves_no_tmp_on_success(self, tmp_path: Path) -> None:
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        ctx.write_review_sidecar({"x": 1})
        # No .tmp files should remain
        tmp_files = list(ctx.run_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_append_vision_audit_creates_sidecar_if_absent(self, tmp_path: Path) -> None:
        """append_vision_audit creates the sidecar with vision_audit when none exists."""
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        assert not ctx.has_review_sidecar()
        ctx.append_vision_audit({"stage": "vision", "calls_made": 3, "cap_reached": False})
        sidecar = ctx.read_review_sidecar()
        assert "vision_audit" in sidecar
        assert sidecar["vision_audit"] == [{"stage": "vision", "calls_made": 3, "cap_reached": False}]

    def test_append_vision_audit_merges_with_existing_sidecar(self, tmp_path: Path) -> None:
        """append_vision_audit preserves existing sidecar keys (edits, audit_trail)."""
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        ctx.write_review_sidecar({"edits": [], "audit_trail": ["prev"]})
        ctx.append_vision_audit({"stage": "vision", "calls_made": 5, "cap_reached": True})
        sidecar = ctx.read_review_sidecar()
        # Existing keys preserved
        assert sidecar["edits"] == []
        assert sidecar["audit_trail"] == ["prev"]
        # Vision audit added
        assert sidecar["vision_audit"] == [{"stage": "vision", "calls_made": 5, "cap_reached": True}]

    def test_append_vision_audit_accumulates_records(self, tmp_path: Path) -> None:
        """Multiple append_vision_audit calls accumulate records in the list."""
        ctx = RunContext(pdf_path=tmp_path / "doc.pdf", output_base=tmp_path / "runs")
        ctx.append_vision_audit({"stage": "vision", "calls_made": 2, "cap_reached": False})
        ctx.append_vision_audit({"stage": "vision", "calls_made": 3, "cap_reached": True})
        sidecar = ctx.read_review_sidecar()
        assert len(sidecar["vision_audit"]) == 2
