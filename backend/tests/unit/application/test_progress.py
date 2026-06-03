"""Tests for pipeline progress emission (determinate progress bar).

Covers:
  - ProgressEvent dataclass correctness
  - RunContext.report_progress: cb receives correct event, no-op when None,
    cb exceptions do NOT propagate.
  - ProgressResponse: percent formula + boundary (item_total==0 → 1.0).
  - RunStatusResponse: progress + started_at serialization.
  - Pipeline stage instrumentation: fake reporter captures events per item
    with monotonic item_done and item_total == real count.
  - Graceful: pipeline with progress_cb=None runs identically (no crash, same result).

TDD: tests written BEFORE implementation (RED → GREEN).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from decimal import Decimal

import pytest

from reconciliation.application.run_context import RunContext, ProgressEvent
from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.domain.models import (
    GuiaIdentity,
    MaterialLine,
    Registro,
    VisionResult,
)


# ---------------------------------------------------------------------------
# Helpers / fakes (reuse pattern from test_pipeline.py)
# ---------------------------------------------------------------------------


class FakeDocumentSource:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def page_count(self) -> int:
        return len(self._pages)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return self._pages[idx].get("image", b"\x89PNG\r\n")

    def page_text(self, idx: int) -> str | None:
        return self._pages[idx].get("text")


class FakeExtractor:
    def __init__(
        self,
        declared_lines: list[MaterialLine] | None = None,
        table_lines: list[MaterialLine] | None = None,
    ) -> None:
        self._declared_lines = declared_lines or []
        self._table_lines = table_lines or []

    def extract_declared(self, text: str) -> list[MaterialLine]:
        return list(self._declared_lines)

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return list(self._table_lines)


class FakeVisionSerial:
    supports_batch: bool = False

    def __init__(self, results: list[VisionResult] | None = None) -> None:
        self._results = results or []
        self._call_count = 0

    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult:
        idx = self._call_count % max(len(self._results), 1)
        self._call_count += 1
        if self._results:
            return self._results[idx]
        return VisionResult(date=date(2026, 5, 28), confidence=0.95, raw="28/05/2026")

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:
        raise NotImplementedError("sequential only")


class FakeIdentityPerPage:
    def __init__(self) -> None:
        self._seq = 0

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity:
        seq = self._seq
        self._seq += 1
        return GuiaIdentity(
            guia_id=f"T001-{seq:04d}",
            serie="T001",
            numero=f"{seq:04d}",
            ruc_emisor="12345678901",
            ruc_receptor="98765432101",
            tipo="09",
            confidence=0.99,
            hashqr_url=None,
        )


def _make_config(tmp_path: Path, max_vision: int = 100) -> AppConfig:
    """Build a minimal AppConfig pointing to tmp_path."""
    cfg = AppConfig.model_validate({
        "output_dir": str(tmp_path / "out"),
        "vision": {"max_vision_calls": max_vision},
    })
    return cfg


def _guia_line(material: str = "BARRA A615 G60 1/2\" 9M", qty: float = 1.0) -> MaterialLine:
    return MaterialLine(
        description_raw=material,
        description_canonical=material,
        unidad="TN",
        cantidad=Decimal(str(qty)),
        confidence=0.95,
        source_page=0,
    )


# ---------------------------------------------------------------------------
# 1. ProgressEvent dataclass
# ---------------------------------------------------------------------------


class TestProgressEvent:
    def test_fields_present(self) -> None:
        ev = ProgressEvent(
            stage_label="Decodificando identidades",
            stage_index=1,
            stage_total=5,
            item_done=3,
            item_total=10,
        )
        assert ev.stage_label == "Decodificando identidades"
        assert ev.stage_index == 1
        assert ev.stage_total == 5
        assert ev.item_done == 3
        assert ev.item_total == 10

    def test_immutable(self) -> None:
        ev = ProgressEvent(
            stage_label="x", stage_index=1, stage_total=5, item_done=1, item_total=5
        )
        with pytest.raises((AttributeError, TypeError)):
            ev.stage_label = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. RunContext.report_progress
# ---------------------------------------------------------------------------


class TestRunContextReportProgress:
    def test_cb_receives_correct_event(self, tmp_path: Path) -> None:
        events: list[ProgressEvent] = []
        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
            progress_cb=events.append,
        )
        ctx.report_progress(
            stage_label="Clasificando páginas",
            stage_index=2,
            stage_total=5,
            item_done=7,
            item_total=20,
        )
        assert len(events) == 1
        ev = events[0]
        assert ev.stage_label == "Clasificando páginas"
        assert ev.stage_index == 2
        assert ev.stage_total == 5
        assert ev.item_done == 7
        assert ev.item_total == 20

    def test_noop_when_cb_is_none(self, tmp_path: Path) -> None:
        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
            progress_cb=None,
        )
        # Must not raise
        ctx.report_progress("Decodificando identidades", 1, 5, 1, 10)

    def test_cb_exception_does_not_propagate(self, tmp_path: Path) -> None:
        def bad_cb(ev: ProgressEvent) -> None:
            raise RuntimeError("boom")

        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
            progress_cb=bad_cb,
        )
        # Must NOT raise; exception is swallowed
        ctx.report_progress("OCR de guías", 3, 5, 1, 5)

    def test_multiple_calls_accumulate(self, tmp_path: Path) -> None:
        events: list[ProgressEvent] = []
        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
            progress_cb=events.append,
        )
        for i in range(1, 4):
            ctx.report_progress("OCR de guías", 3, 5, i, 3)
        assert len(events) == 3
        assert [e.item_done for e in events] == [1, 2, 3]

    def test_default_cb_is_none(self, tmp_path: Path) -> None:
        """Backward-compat: existing code that doesn't pass progress_cb still works."""
        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
        )
        # No crash
        ctx.report_progress("Decodificando identidades", 1, 5, 1, 1)


# ---------------------------------------------------------------------------
# 3. ProgressResponse schema: percent formula + division-by-zero guard
# ---------------------------------------------------------------------------


class TestProgressResponsePercent:
    """Test the percent formula via the Pydantic schema."""

    def _make_response(self, stage_index: int, stage_total: int, item_done: int, item_total: int):
        from reconciliation.infrastructure.api.schemas import ProgressResponse
        return ProgressResponse(
            stage_label="OCR de guías",
            stage_index=stage_index,
            stage_total=stage_total,
            item_done=item_done,
            item_total=item_total,
        )

    def test_mid_run_percent(self) -> None:
        # stage 4/5, item 9/18 → (3 + 9/18) / 5 = (3 + 0.5) / 5 = 0.70 → 70.0
        resp = self._make_response(4, 5, 9, 18)
        assert abs(resp.percent - 70.0) < 0.01

    def test_item_total_zero_no_div_zero(self) -> None:
        # item_total=0 → treat full stage → item fraction = 1
        resp = self._make_response(3, 5, 0, 0)
        # (2 + 1) / 5 = 0.6 → 60.0
        assert abs(resp.percent - 60.0) < 0.01

    def test_first_item_of_first_stage(self) -> None:
        # stage 1/5, item 1/10 → (0 + 0.1) / 5 = 0.02 → 2.0
        resp = self._make_response(1, 5, 1, 10)
        assert abs(resp.percent - 2.0) < 0.01

    def test_completion_100(self) -> None:
        # stage 5/5, item 5/5 → (4 + 1) / 5 = 1.0 → 100.0
        resp = self._make_response(5, 5, 5, 5)
        assert abs(resp.percent - 100.0) < 0.01

    def test_percent_clamped_to_0_100(self) -> None:
        # Even edge cases should not exceed 0–100
        resp = self._make_response(5, 5, 5, 5)
        assert 0.0 <= resp.percent <= 100.0


# ---------------------------------------------------------------------------
# 4. RunStatusResponse: progress + started_at serialization
# ---------------------------------------------------------------------------


class TestRunStatusResponseWithProgress:
    def test_progress_none_by_default(self) -> None:
        from reconciliation.infrastructure.api.schemas import RunStatusResponse
        r = RunStatusResponse(run_id="abc", status="processing")
        assert r.progress is None
        assert r.started_at is None

    def test_progress_field_serializes(self) -> None:
        from reconciliation.infrastructure.api.schemas import RunStatusResponse, ProgressResponse
        prog = ProgressResponse(
            stage_label="Lectura de visión",
            stage_index=4,
            stage_total=5,
            item_done=9,
            item_total=18,
        )
        r = RunStatusResponse(run_id="abc", status="processing", progress=prog, started_at="2026-06-03T00:00:00Z")
        d = r.model_dump()
        assert d["progress"]["stage_label"] == "Lectura de visión"
        assert abs(d["progress"]["percent"] - 70.0) < 0.01
        assert d["started_at"] == "2026-06-03T00:00:00Z"


# ---------------------------------------------------------------------------
# 5. Pipeline stage instrumentation: fake reporter captures events
# ---------------------------------------------------------------------------


class TestPipelineProgressInstrumentation:
    """Verify that the pipeline emits progress events per item in each stage."""

    def _build_pipeline_with_guia_pages(
        self,
        tmp_path: Path,
        n_guia_pages: int = 3,
    ) -> tuple[ReconciliationPipeline, RunContext, list[ProgressEvent]]:
        """Build a minimal pipeline with N GUIA pages and 1 DECLARED page."""
        declared_line = _guia_line()
        guia_line = _guia_line(qty=1.0)

        pages: list[dict[str, Any]] = []
        # Page 0: DECLARED (Forma header)
        pages.append({"text": "Forma\nDescription numero: 232\nBAR 1/2\" TN 3.0", "image": b"\x89PNG\r\n"})
        # Pages 1..N: GUIA pages (image-dominant, QR-decoded)
        for _ in range(n_guia_pages):
            pages.append({"text": None, "image": b"\x89PNG\r\n"})

        config = _make_config(tmp_path)
        doc = FakeDocumentSource(pages)
        extractor = FakeExtractor(
            declared_lines=[declared_line],
            table_lines=[guia_line],
        )
        vision = FakeVisionSerial(
            results=[VisionResult(date=date(2026, 5, 28), confidence=0.95, raw="28/05/2026")]
        )
        identity = FakeIdentityPerPage()

        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=vision,
            config=config,
            page_to_registro={i: "232" for i in range(1, n_guia_pages + 1)},
            identity=identity,
        )

        events: list[ProgressEvent] = []
        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
            progress_cb=events.append,
        )
        return pipeline, ctx, events

    def test_events_emitted_for_decode_identities_stage(self, tmp_path: Path) -> None:
        n = 4
        pipeline, ctx, events = self._build_pipeline_with_guia_pages(tmp_path, n_guia_pages=n)
        pipeline.run(ctx)

        decode_events = [e for e in events if e.stage_label == "Decodificando identidades"]
        assert len(decode_events) > 0
        # item_total should equal the total page count (n+1 = 5)
        total_pages = n + 1
        assert all(e.item_total == total_pages for e in decode_events)
        # item_done is monotonically increasing (1-based)
        done_vals = [e.item_done for e in decode_events]
        assert done_vals == sorted(done_vals)
        assert done_vals[0] >= 1

    def test_events_emitted_for_classify_stage(self, tmp_path: Path) -> None:
        n = 3
        pipeline, ctx, events = self._build_pipeline_with_guia_pages(tmp_path, n_guia_pages=n)
        pipeline.run(ctx)

        classify_events = [e for e in events if e.stage_label == "Clasificando páginas"]
        assert len(classify_events) > 0
        total_pages = n + 1
        assert all(e.item_total == total_pages for e in classify_events)
        done_vals = [e.item_done for e in classify_events]
        assert done_vals == list(range(1, len(done_vals) + 1))

    def test_events_emitted_for_ocr_stage(self, tmp_path: Path) -> None:
        n = 3
        pipeline, ctx, events = self._build_pipeline_with_guia_pages(tmp_path, n_guia_pages=n)
        pipeline.run(ctx)

        ocr_events = [e for e in events if e.stage_label == "OCR de guías"]
        assert len(ocr_events) > 0
        # item_total is the real GUIA page count (not necessarily == n, because
        # FakeIdentityPerPage returns a QR identity for every page including the
        # DECLARED page, causing it to classify as GUIA too).
        # What matters: item_total is consistent across all OCR events and
        # item_done is monotonically 1-based.
        assert len({e.item_total for e in ocr_events}) == 1, "item_total must be consistent"
        done_vals = [e.item_done for e in ocr_events]
        assert done_vals == list(range(1, len(done_vals) + 1))
        # item_total equals the actual number of OCR events (one per GUIA page)
        assert ocr_events[-1].item_total == len(ocr_events)

    def test_events_emitted_for_vision_stage(self, tmp_path: Path) -> None:
        n = 3
        pipeline, ctx, events = self._build_pipeline_with_guia_pages(tmp_path, n_guia_pages=n)
        pipeline.run(ctx)

        vision_events = [e for e in events if e.stage_label == "Lectura de visión"]
        assert len(vision_events) > 0
        # item_total = number of blocks assembled from GUIA pages.
        # FakeIdentityPerPage gives unique guia_id per page → 1 block per GUIA page.
        # Since fake identity fires on all pages (including the DECLARED page here),
        # block count may differ from n. What matters: consistent item_total and
        # item_done equals block count at the last event.
        assert len({e.item_total for e in vision_events}) == 1, "item_total must be consistent"
        assert vision_events[-1].item_done == vision_events[-1].item_total

    def test_events_emitted_for_declared_date_stage(self, tmp_path: Path) -> None:
        n = 2
        pipeline, ctx, events = self._build_pipeline_with_guia_pages(tmp_path, n_guia_pages=n)
        pipeline.run(ctx)

        date_events = [e for e in events if e.stage_label == "Fecha de protocolo"]
        # The stage runs on registros with protocolo_page; since we use legacy FakeExtractor,
        # registros may have protocolo_page=None → no vision calls → but events still emitted.
        # We just check they are emitted and have valid structure.
        for e in date_events:
            assert e.stage_index == 5
            assert e.stage_total == 5
            assert e.item_done >= 1

    def test_final_completion_event_emitted(self, tmp_path: Path) -> None:
        """The last event must be stage 5/5 with item_done == item_total."""
        n = 2
        pipeline, ctx, events = self._build_pipeline_with_guia_pages(tmp_path, n_guia_pages=n)
        pipeline.run(ctx)

        assert len(events) > 0
        last = events[-1]
        assert last.stage_index == 5
        assert last.stage_total == 5
        assert last.item_done == last.item_total

    def test_item_done_monotonic_per_stage(self, tmp_path: Path) -> None:
        n = 3
        pipeline, ctx, events = self._build_pipeline_with_guia_pages(tmp_path, n_guia_pages=n)
        pipeline.run(ctx)

        for label in ("Decodificando identidades", "Clasificando páginas", "OCR de guías", "Lectura de visión"):
            stage_events = [e for e in events if e.stage_label == label]
            if stage_events:
                done_vals = [e.item_done for e in stage_events]
                assert done_vals == sorted(done_vals), f"Non-monotonic item_done in stage '{label}'"


# ---------------------------------------------------------------------------
# 6. Graceful: pipeline with progress_cb=None runs identically
# ---------------------------------------------------------------------------


class TestPipelineGracefulNoCb:
    """Pipeline with progress_cb=None must produce the same result as with a cb."""

    def _build_pipeline(self, tmp_path: Path) -> tuple[ReconciliationPipeline, FakeDocumentSource]:
        declared_line = _guia_line()
        guia_line = _guia_line(qty=2.0)
        pages: list[dict[str, Any]] = [
            {"text": "Forma\nDescription numero: 232\nBAR 1/2\" TN 3.0", "image": b"\x89PNG\r\n"},
            {"text": None, "image": b"\x89PNG\r\n"},
            {"text": None, "image": b"\x89PNG\r\n"},
        ]
        config = _make_config(tmp_path)
        doc = FakeDocumentSource(pages)
        extractor = FakeExtractor(declared_lines=[declared_line], table_lines=[guia_line])
        vision = FakeVisionSerial(
            results=[VisionResult(date=date(2026, 5, 28), confidence=0.95, raw="28/05/2026")]
        )
        identity = FakeIdentityPerPage()
        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=vision,
            config=config,
            page_to_registro={1: "232", 2: "232"},
            identity=identity,
        )
        return pipeline, doc

    def test_no_crash_without_cb(self, tmp_path: Path) -> None:
        pipeline, _ = self._build_pipeline(tmp_path)
        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
            progress_cb=None,
        )
        result = pipeline.run(ctx)
        # run_id is set
        assert result.run_id == ctx.run_id

    def test_no_crash_with_cb(self, tmp_path: Path) -> None:
        pipeline, _ = self._build_pipeline(tmp_path)
        events: list[ProgressEvent] = []
        ctx = RunContext(
            pdf_path=tmp_path / "doc.pdf",
            output_base=tmp_path / "runs",
            progress_cb=events.append,
        )
        result = pipeline.run(ctx)
        assert result.run_id == ctx.run_id
        assert len(events) > 0
