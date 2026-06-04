"""Tests for SUNAT-fetch progress reporting (Fix B — issue #21).

Verifies:
  - With SUNAT enabled + N blocks, _stage_sunat_fetch calls ctx.report_progress
    with stage_label="Consulta SUNAT" and item_done advancing from 1 to N.
  - With SUNAT disabled, no "Consulta SUNAT" progress events are emitted
    (no phantom stage stalls the bar).
  - The stage numbering is monotonic: SUNAT stage_index < vision stage_index <
    final stage_index across a full pipeline run with SUNAT enabled.
  - stage_total is 6 when SUNAT is enabled, 5 when disabled.

Fix #21 (REDO) — per-wave/per-iteration progress:
  - The pipeline passes a non-None on_progress callback to fetch_many (concurrent path).
  - The adapter (descargaqr.py) calls on_progress per wave with cumulative counts.
  - The sequential fallback emits progress per-iteration DURING the loop.
  - Progress is emitted DURING the fetch, not only after.

TDD: tests written BEFORE implementation (RED → GREEN).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline, _GuiaBlock  # type: ignore[attr-defined]
from reconciliation.application.run_context import ProgressEvent, RunContext
from reconciliation.domain.models import (
    GreLineItem,
    GuiaIdentity,
    MaterialLine,
    OfficialGre,
    VisionResult,
)


# ---------------------------------------------------------------------------
# Fakes and helpers
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

    def __init__(self, result: VisionResult | None = None) -> None:
        self._result = result or VisionResult(
            date=date(2026, 5, 28), confidence=0.95, raw="28/05/2026"
        )

    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult:
        return self._result

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
            hashqr_url=f"https://sunat.gob.pe/descargaqr?numRuc=12345678901&numDoc={seq:04d}",
        )


class FakeSunatPort:
    """Fake SUNAT port that returns a minimal OfficialGre per URL."""

    def __init__(self, delay_calls: int = 0) -> None:
        self._calls: list[str] = []

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        self._calls.append(hashqr_url)
        # Return a minimal OfficialGre with one line item (use factory helper)
        gre = OfficialGre.from_identity("T001-0000")
        gre = gre.model_copy(update={
            "fecha_emision": date(2026, 5, 25),
            "fecha_entrega": date(2026, 5, 27),
            "lines": [
                GreLineItem(
                    descripcion="BARRA 1/2\" 9M",
                    unidad="TN",
                    cantidad=Decimal("1.0"),
                )
            ],
        })
        return gre

    @property
    def calls(self) -> list[str]:
        return list(self._calls)


class FakeCapturingCtx:
    """Minimal RunContext-like that captures report_progress calls."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self.run_id = "fake-run-id"

    def report_progress(
        self,
        stage_label: str,
        stage_index: int,
        stage_total: int,
        item_done: int,
        item_total: int,
    ) -> None:
        self.events.append(
            ProgressEvent(
                stage_label=stage_label,
                stage_index=stage_index,
                stage_total=stage_total,
                item_done=item_done,
                item_total=item_total,
            )
        )


def _make_config(tmp_path: Path, sunat_enabled: bool = True) -> AppConfig:
    cfg_dict: dict[str, Any] = {
        "output_dir": str(tmp_path / "out"),
        "vision": {"max_vision_calls": 100},
        "sunat": {"enabled": sunat_enabled},
    }
    if sunat_enabled:
        # vision must be enabled when sunat is on (no conflict with R9b guard)
        pass
    return AppConfig.model_validate(cfg_dict)


def _guia_line(material: str = "BARRA A615 G60 1/2\" 9M", qty: float = 1.0) -> MaterialLine:
    return MaterialLine(
        description_raw=material,
        description_canonical=material,
        unidad="TN",
        cantidad=Decimal(str(qty)),
        confidence=0.95,
        source_page=0,
    )


def _build_pipeline(
    tmp_path: Path,
    n_guia_pages: int = 2,
    sunat_enabled: bool = True,
    fake_sunat: FakeSunatPort | None = None,
) -> tuple[ReconciliationPipeline, RunContext, list[ProgressEvent]]:
    """Build a minimal pipeline with N guía pages and 1 declared page."""
    declared_line = _guia_line()
    guia_line = _guia_line(qty=1.0)

    pages: list[dict[str, Any]] = []
    pages.append({"text": "Forma\nDescription numero: 232\nBAR 1/2\" TN 3.0", "image": b"\x89PNG\r\n"})
    for _ in range(n_guia_pages):
        pages.append({"text": None, "image": b"\x89PNG\r\n"})

    config = _make_config(tmp_path, sunat_enabled=sunat_enabled)
    doc = FakeDocumentSource(pages)
    extractor = FakeExtractor(declared_lines=[declared_line], table_lines=[guia_line])
    vision = FakeVisionSerial()
    identity = FakeIdentityPerPage()
    sunat_port = fake_sunat if sunat_enabled else None

    pipeline = ReconciliationPipeline(
        doc_source=doc,
        extractor=extractor,
        vision=vision,
        config=config,
        page_to_registro={i: "232" for i in range(1, n_guia_pages + 1)},
        identity=identity,
        sunat=sunat_port,
    )

    events: list[ProgressEvent] = []
    ctx = RunContext(
        pdf_path=tmp_path / "doc.pdf",
        output_base=tmp_path / "runs",
        progress_cb=events.append,
    )
    return pipeline, ctx, events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSunatProgressEnabled:
    """With SUNAT enabled, _stage_sunat_fetch emits 'Consulta SUNAT' progress events."""

    def test_sunat_progress_events_emitted(self, tmp_path: Path) -> None:
        """At least one 'Consulta SUNAT' event is emitted when SUNAT is enabled and blocks exist."""
        fake_sunat = FakeSunatPort()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        sunat_events = [e for e in events if e.stage_label == "Consulta SUNAT"]
        assert len(sunat_events) > 0, (
            "Expected 'Consulta SUNAT' progress events when SUNAT is enabled"
        )

    def test_sunat_item_done_advances(self, tmp_path: Path) -> None:
        """item_done must be monotonically increasing (1-based) for SUNAT events."""
        fake_sunat = FakeSunatPort()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=3, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        sunat_events = [e for e in events if e.stage_label == "Consulta SUNAT"]
        assert sunat_events, "Must have SUNAT events"
        done_vals = [e.item_done for e in sunat_events]
        assert done_vals == sorted(done_vals), "item_done must be monotonically non-decreasing"
        # The stage emits an immediate 0/N so the label switches the moment the fetch
        # starts (before the first slow wave completes), then advances to N.
        assert done_vals[0] == 0, "stage starts at 0/N (immediate label switch)"
        assert done_vals[-1] >= 1, "item_done must advance past the initial 0"

    def test_sunat_stage_total_is_6(self, tmp_path: Path) -> None:
        """When SUNAT is enabled, stage_total must be 6 for all SUNAT events."""
        fake_sunat = FakeSunatPort()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        sunat_events = [e for e in events if e.stage_label == "Consulta SUNAT"]
        assert sunat_events, "Must have SUNAT events"
        for ev in sunat_events:
            assert ev.stage_total == 6, (
                f"stage_total must be 6 when SUNAT is enabled, got {ev.stage_total}"
            )

    def test_vision_stage_index_is_5_when_sunat_enabled(self, tmp_path: Path) -> None:
        """With SUNAT enabled, 'Lectura de visión' must be stage 5 (SUNAT is 4)."""
        fake_sunat = FakeSunatPort()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        vision_events = [e for e in events if e.stage_label == "Lectura de visión"]
        assert vision_events, "Must have vision events"
        for ev in vision_events:
            assert ev.stage_index == 5, (
                f"Vision stage_index must be 5 when SUNAT is enabled, got {ev.stage_index}"
            )
            assert ev.stage_total == 6

    def test_final_event_is_stage_6_when_sunat_enabled(self, tmp_path: Path) -> None:
        """Final completion event must be stage 6/6 when SUNAT is enabled."""
        fake_sunat = FakeSunatPort()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        assert events, "Must have at least one event"
        last = events[-1]
        assert last.stage_index == 6, f"Final stage_index must be 6, got {last.stage_index}"
        assert last.stage_total == 6
        assert last.item_done == last.item_total

    def test_stage_sequence_monotonic_when_sunat_enabled(self, tmp_path: Path) -> None:
        """stage_index must be monotonically non-decreasing across all events."""
        fake_sunat = FakeSunatPort()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        assert events, "Must have events"
        indices = [e.stage_index for e in events]
        assert indices == sorted(indices), (
            f"stage_index must be monotonically non-decreasing; got {indices}"
        )


class TestSunatProgressDisabled:
    """With SUNAT disabled, no 'Consulta SUNAT' events are emitted; stage_total stays 5."""

    def test_no_sunat_events_when_disabled(self, tmp_path: Path) -> None:
        """No 'Consulta SUNAT' progress events emitted when SUNAT is off."""
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=False
        )
        pipeline.run(ctx)

        sunat_events = [e for e in events if e.stage_label == "Consulta SUNAT"]
        assert len(sunat_events) == 0, (
            "Must NOT emit 'Consulta SUNAT' events when SUNAT is disabled"
        )

    def test_stage_total_is_5_when_sunat_disabled(self, tmp_path: Path) -> None:
        """When SUNAT is disabled, stage_total must remain 5 for all events."""
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=False
        )
        pipeline.run(ctx)

        assert events, "Must have at least one event"
        for ev in events:
            assert ev.stage_total == 5, (
                f"stage_total must be 5 when SUNAT is disabled, got {ev.stage_total}"
            )

    def test_final_event_is_stage_5_when_sunat_disabled(self, tmp_path: Path) -> None:
        """Final completion event must be stage 5/5 when SUNAT is disabled."""
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=False
        )
        pipeline.run(ctx)

        last = events[-1]
        assert last.stage_index == 5, f"Final stage_index must be 5, got {last.stage_index}"
        assert last.stage_total == 5
        assert last.item_done == last.item_total

    def test_vision_stage_index_is_4_when_sunat_disabled(self, tmp_path: Path) -> None:
        """When SUNAT is disabled, 'Lectura de visión' is still stage 4 (unchanged)."""
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=2, sunat_enabled=False
        )
        pipeline.run(ctx)

        vision_events = [e for e in events if e.stage_label == "Lectura de visión"]
        assert vision_events, "Must have vision events"
        for ev in vision_events:
            assert ev.stage_index == 4, (
                f"Vision stage_index must be 4 when SUNAT is disabled, got {ev.stage_index}"
            )
            assert ev.stage_total == 5


# ---------------------------------------------------------------------------
# Fix #21 (REDO): per-wave / per-iteration progress — fakes and tests
# ---------------------------------------------------------------------------


class FakeSunatWithFetchMany:
    """Fake SUNAT adapter with fetch_many that records on_progress usage.

    This fake simulates wave-level progress reporting: fetch_many calls
    on_progress(k, total) for k=1..N before returning, so the pipeline's
    progress callback fires DURING the (simulated) fetch, not after.

    The key assertion: the pipeline must pass a non-None on_progress to
    fetch_many. The OLD code never passed on_progress — so any test that
    asserts on_progress is not None fails on the old code.
    """

    def __init__(self, n_urls_expected: int = 0) -> None:
        self._calls: list[str] = []
        self.received_on_progress: Callable[[int, int], None] | None = None
        self.on_progress_call_args: list[tuple[int, int]] = []

    def _make_gre(self) -> OfficialGre:
        gre = OfficialGre.from_identity("T001-0000")
        return gre.model_copy(update={
            "fecha_emision": date(2026, 5, 25),
            "fecha_entrega": date(2026, 5, 27),
            "lines": [
                GreLineItem(
                    descripcion="BARRA 1/2\" 9M",
                    unidad="TN",
                    cantidad=Decimal("1.0"),
                )
            ],
        })

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        self._calls.append(hashqr_url)
        return self._make_gre()

    async def fetch_many(
        self,
        urls: list[str],
        concurrency: int = 5,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, OfficialGre | None]:
        """Async fake: records on_progress and calls it per wave (simulating real adapter)."""
        self.received_on_progress = on_progress
        results: dict[str, OfficialGre | None] = {}
        total = len(urls)
        wave_size = max(1, concurrency)
        pending = list(urls)
        done = 0

        while pending:
            wave = pending[:wave_size]
            pending = pending[wave_size:]
            for url in wave:
                results[url] = self._make_gre()
                done += 1
            # Simulate per-wave progress report (as the real adapter will do)
            if on_progress is not None:
                self.on_progress_call_args.append((done, total))
                on_progress(done, total)

        return results


class FakeSunatSequentialOnly:
    """Fake SUNAT adapter with only fetch() — no fetch_many.

    Forces the sequential fallback path in _stage_sunat_fetch.
    Records the order of fetch() calls and tracks report_progress interleaving.
    """

    def __init__(self) -> None:
        self._fetch_call_order: list[str] = []

    def _make_gre(self) -> OfficialGre:
        gre = OfficialGre.from_identity("T001-0000")
        return gre.model_copy(update={
            "fecha_emision": date(2026, 5, 25),
            "fecha_entrega": date(2026, 5, 27),
            "lines": [
                GreLineItem(
                    descripcion="BARRA 1/2\" 9M",
                    unidad="TN",
                    cantidad=Decimal("1.0"),
                )
            ],
        })

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        self._fetch_call_order.append(("fetch", hashqr_url))
        return self._make_gre()

    @property
    def fetch_call_order(self) -> list[tuple[str, str]]:
        return list(self._fetch_call_order)


class TestPerWaveProgressFix:
    """Critical regression tests for issue #21 fix (REDO).

    These tests fail on the OLD code (pre-fix) and pass after the fix.
    The key invariant: progress must be driven BY the adapter during the fetch,
    not only after fetch_many returns.
    """

    def test_pipeline_passes_on_progress_to_fetch_many(self, tmp_path: Path) -> None:
        """CRITICAL: _stage_sunat_fetch must pass a non-None on_progress to fetch_many.

        The OLD code called fetch_many(urls, concurrency=5) with NO on_progress.
        This test FAILS on the old code because received_on_progress stays None.
        """
        fake_sunat = FakeSunatWithFetchMany()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=3, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        assert fake_sunat.received_on_progress is not None, (
            "Pipeline must pass on_progress callback to fetch_many. "
            "The old code passed None — this is the root cause of issue #21 (REDO)."
        )

    def test_on_progress_called_with_cumulative_counts_during_fetch(self, tmp_path: Path) -> None:
        """on_progress receives (done, total) with cumulative done count per wave.

        The adapter calls on_progress(done_so_far, total) after each wave.
        This fires DURING fetch_many execution, not after it returns.
        """
        fake_sunat = FakeSunatWithFetchMany()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=4, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        # on_progress_call_args are recorded by the fake adapter
        assert fake_sunat.on_progress_call_args, (
            "on_progress must have been called by fetch_many during the fetch"
        )

        # All calls have total = number of URLs (4 guía pages → 4 URLs)
        totals = {total for _, total in fake_sunat.on_progress_call_args}
        assert len(totals) == 1, f"total must be consistent across calls, got {totals}"

        # done must be monotonically increasing (cumulative per-wave)
        done_vals = [done for done, _ in fake_sunat.on_progress_call_args]
        assert done_vals == sorted(done_vals), (
            f"done values must be monotonically non-decreasing, got {done_vals}"
        )
        assert done_vals[-1] == fake_sunat.on_progress_call_args[-1][1], (
            "Final on_progress call must have done == total"
        )

    def test_sunat_progress_events_driven_by_adapter_callback(self, tmp_path: Path) -> None:
        """ctx.report_progress('Consulta SUNAT') calls must be driven by on_progress.

        Verifies that the events in ctx come from the on_progress callback the
        pipeline passed to fetch_many — not from a post-fetch loop only.
        The events must appear with ADVANCING item_done values matching the
        per-wave cumulative counts.
        """
        n_pages = 4
        fake_sunat = FakeSunatWithFetchMany()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=n_pages, sunat_enabled=True, fake_sunat=fake_sunat
        )
        pipeline.run(ctx)

        sunat_events = [e for e in events if e.stage_label == "Consulta SUNAT"]
        assert sunat_events, "Must have 'Consulta SUNAT' events"

        done_vals = [e.item_done for e in sunat_events]
        assert done_vals == sorted(done_vals), (
            f"item_done must be monotonically non-decreasing: {done_vals}"
        )
        assert done_vals[0] == 0, "stage emits an immediate 0/N before the first wave"
        # Final event must reach item_total
        last = sunat_events[-1]
        assert last.item_done == last.item_total, (
            f"Final event must have item_done == item_total, got {last.item_done}/{last.item_total}"
        )

    def test_sequential_fallback_emits_progress_per_iteration(self, tmp_path: Path) -> None:
        """Sequential path: report_progress fires per-URL DURING the loop.

        FakeSunatSequentialOnly has NO fetch_many — forces sequential fallback.
        The old code used a dict-comprehension (all-at-once then post-loop);
        the fixed code uses an explicit loop that calls report_progress per URL.
        We assert that 'Consulta SUNAT' events are emitted with item_done = 1, 2, ... N.
        """
        fake_sunat = FakeSunatSequentialOnly()
        pipeline, ctx, events = _build_pipeline(
            tmp_path, n_guia_pages=3, sunat_enabled=True, fake_sunat=fake_sunat  # type: ignore[arg-type]
        )
        pipeline.run(ctx)

        sunat_events = [e for e in events if e.stage_label == "Consulta SUNAT"]
        assert sunat_events, "Must have 'Consulta SUNAT' events for sequential path"

        done_vals = [e.item_done for e in sunat_events]
        # Immediate 0/N, then one event per URL: 0, 1, 2, ... N
        assert done_vals == list(range(0, len(done_vals))), (
            f"Sequential path must emit 0/N then one event per URL: got {done_vals}"
        )
        # All events share the same item_total = number of URLs
        assert all(e.item_total == sunat_events[-1].item_total for e in sunat_events), (
            "item_total must be consistent across all sequential events"
        )


class TestDescargaqrFetchManyOnProgress:
    """Unit tests for descargaqr.fetch_many on_progress wiring (adapter level).

    These tests verify the adapter itself calls on_progress per wave with
    cumulative counts. They mock the HTTP so no real network is needed.
    """

    def test_on_progress_called_once_per_wave_with_cumulative_count(self) -> None:
        """9 URLs, concurrency=5 → wave 1 (5 URLs) + wave 2 (4 URLs).

        on_progress must be called twice: (5, 9) then (9, 9).
        """
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter

        progress_calls: list[tuple[int, int]] = []

        def fake_on_progress(done: int, total: int) -> None:
            progress_calls.append((done, total))

        # Stub out the real fetch() to avoid HTTP
        adapter = SunatDescargaqrAdapter.__new__(SunatDescargaqrAdapter)

        def fake_fetch(url: str) -> OfficialGre | None:
            gre = OfficialGre.from_identity("T001-0000")
            return gre.model_copy(update={
                "fecha_emision": date(2026, 5, 25),
                "fecha_entrega": date(2026, 5, 27),
                "lines": [],
            })

        adapter.fetch = fake_fetch  # type: ignore[method-assign]
        # Also stub _pace_gate-level attributes needed by fetch_many internals
        # The pacing lock is not needed for a mocked fetch; stub it
        import threading
        adapter._pace_lock = threading.Lock()  # type: ignore[attr-defined]
        adapter._last_download_monotonic = None  # type: ignore[attr-defined]

        urls = [f"https://sunat.example.com/q?id={i}" for i in range(9)]

        results = asyncio.run(
            adapter.fetch_many(urls, concurrency=5, on_progress=fake_on_progress)
        )

        assert len(results) == 9, "Must return results for all 9 URLs"
        assert len(progress_calls) == 2, (
            f"Expected 2 on_progress calls (wave 1 + wave 2), got {len(progress_calls)}: {progress_calls}"
        )
        assert progress_calls[0] == (5, 9), (
            f"First call must be (5, 9), got {progress_calls[0]}"
        )
        assert progress_calls[1] == (9, 9), (
            f"Second call must be (9, 9), got {progress_calls[1]}"
        )

    def test_on_progress_not_called_when_none(self) -> None:
        """When on_progress=None, fetch_many must not raise and must still return results."""
        from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter
        import threading

        adapter = SunatDescargaqrAdapter.__new__(SunatDescargaqrAdapter)

        def fake_fetch(url: str) -> OfficialGre | None:
            gre = OfficialGre.from_identity("T001-0000")
            return gre.model_copy(update={
                "fecha_emision": date(2026, 5, 25),
                "fecha_entrega": date(2026, 5, 27),
                "lines": [],
            })

        adapter.fetch = fake_fetch  # type: ignore[method-assign]
        adapter._pace_lock = threading.Lock()  # type: ignore[attr-defined]
        adapter._last_download_monotonic = None  # type: ignore[attr-defined]

        urls = [f"https://sunat.example.com/q?id={i}" for i in range(3)]
        # Must not raise even when on_progress=None (backward compatible)
        results = asyncio.run(adapter.fetch_many(urls, concurrency=5, on_progress=None))
        assert len(results) == 3
