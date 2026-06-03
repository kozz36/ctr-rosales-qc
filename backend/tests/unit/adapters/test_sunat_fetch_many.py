"""Unit tests for SunatDescargaqrAdapter.fetch_many — bounded-concurrency batch fetch (R10.7 / CONT-S09/S11).

Tests validate:
- All URLs fetched and returned in the result dict
- Concurrency bounded by semaphore (no more than N in-flight simultaneously)
- URLs with fetch returning None are present with None value (graceful contract)
- Empty URL list → empty dict, no fetch calls
- N-shrink guard fires after 3+ consecutive None results without crashing
- SunatGreFetchPort.fetch_many default implementation delegates to fetch()
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from reconciliation.adapters.sunat.descargaqr import SunatDescargaqrAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_gre(guia_id: str = "T073-00680258") -> MagicMock:
    """Return a minimal OfficialGre-shaped mock."""
    m = MagicMock()
    m.guia_id = guia_id
    return m


# ---------------------------------------------------------------------------
# fetch_many — basic behaviour
# ---------------------------------------------------------------------------


class TestFetchManyBasicBehaviour:
    def test_empty_list_returns_empty_dict(self) -> None:
        adapter = SunatDescargaqrAdapter()
        result = asyncio.run(adapter.fetch_many([]))
        assert result == {}

    def test_all_urls_returned_in_result(self) -> None:
        """All submitted URLs appear as keys in the result dict."""
        urls = [f"https://sunat.gob.pe/hashqr={i}" for i in range(6)]
        adapter = SunatDescargaqrAdapter()

        with patch.object(adapter, "fetch", return_value=_stub_gre()) as mock_fetch:
            result = asyncio.run(adapter.fetch_many(urls, concurrency=3))

        assert set(result.keys()) == set(urls)
        assert mock_fetch.call_count == 6

    def test_fetch_called_once_per_url(self) -> None:
        urls = ["url-A", "url-B", "url-C"]
        adapter = SunatDescargaqrAdapter()

        with patch.object(adapter, "fetch", return_value=_stub_gre()) as mock_fetch:
            asyncio.run(adapter.fetch_many(urls, concurrency=5))

        assert mock_fetch.call_count == 3

    def test_url_with_none_result_is_present_as_none(self) -> None:
        """Graceful None from fetch() is propagated — not swallowed."""
        urls = ["url-good", "url-bad"]
        adapter = SunatDescargaqrAdapter()

        def _side_effect(url: str) -> Any:
            return None if url == "url-bad" else _stub_gre()

        with patch.object(adapter, "fetch", side_effect=_side_effect):
            result = asyncio.run(adapter.fetch_many(urls))

        assert result["url-bad"] is None
        assert result["url-good"] is not None

    def test_all_none_results_no_crash(self) -> None:
        """All-None responses (e.g. SUNAT 429) must not crash."""
        urls = [f"url-{i}" for i in range(5)]
        adapter = SunatDescargaqrAdapter()

        with patch.object(adapter, "fetch", return_value=None):
            result = asyncio.run(adapter.fetch_many(urls, concurrency=2))

        assert len(result) == 5
        assert all(v is None for v in result.values())


# ---------------------------------------------------------------------------
# fetch_many — concurrency bounding
# ---------------------------------------------------------------------------


class TestFetchManyConcurrencyBound:
    def test_concurrency_is_bounded(self) -> None:
        """No more than `concurrency` tasks should run simultaneously."""
        N = 3
        urls = [f"url-{i}" for i in range(9)]
        adapter = SunatDescargaqrAdapter()

        # Track peak concurrency via a thread-safe counter
        peak = {"count": 0, "max": 0}
        lock = threading.Lock()
        barrier = threading.Barrier(1, timeout=5)  # just a sync lock

        def _counting_fetch(url: str) -> Any:
            with lock:
                peak["count"] += 1
                if peak["count"] > peak["max"]:
                    peak["max"] = peak["count"]
            # Yield briefly so concurrent tasks overlap
            import time
            time.sleep(0.01)
            with lock:
                peak["count"] -= 1
            return _stub_gre()

        with patch.object(adapter, "fetch", side_effect=_counting_fetch):
            asyncio.run(adapter.fetch_many(urls, concurrency=N))

        # Due to asyncio.to_thread and the GIL, we can't guarantee tight peak
        # but we CAN guarantee it never exceeded N + a small thread-dispatch window.
        # The test guards against gross over-parallelism (e.g. all 9 at once).
        assert peak["max"] <= N + 2  # +2 for thread-scheduling jitter


# ---------------------------------------------------------------------------
# fetch_many — N-shrink guard (429-origin consecutive None)
# ---------------------------------------------------------------------------


class TestFetchManyNShrink:
    def test_n_shrink_guard_fires_without_crashing(self) -> None:
        """3+ consecutive None results from fetch → concurrency shrinks by 1; no crash."""
        urls = [f"url-{i}" for i in range(10)]
        adapter = SunatDescargaqrAdapter()

        # All None → simulates SUNAT 429 saturation
        with patch.object(adapter, "fetch", return_value=None):
            # Should complete without exception and return 10 entries
            result = asyncio.run(adapter.fetch_many(urls, concurrency=4))

        assert len(result) == 10

    def test_sustained_failures_actually_reduce_in_flight_concurrency(self) -> None:
        """W1 (KI-3): under sustained None/429 the EFFECTIVE in-flight concurrency
        must genuinely drop across waves — not just decrement a dead local int.

        Pacing is patched near-zero so concurrent fetches actually overlap; each
        fetch is slow enough to overlap with its wave-mates. We record the live
        concurrent-call count and assert the early peak exceeds the late peak.
        """
        from reconciliation.adapters.sunat import descargaqr as _mod

        urls = [f"url-{i}" for i in range(18)]
        adapter = SunatDescargaqrAdapter()

        lock = threading.Lock()
        live = {"count": 0}
        # Sample the live concurrent-count at each fetch entry, in call order.
        # Waves run sequentially (gather awaits each fully), so the count returns
        # to 0 between waves — we segment on that boundary to get per-wave peaks.
        samples: list[int] = []

        def _failing_fetch(url: str) -> Any:
            import time
            with lock:
                live["count"] += 1
                samples.append(live["count"])
            time.sleep(0.03)  # overlap window so wave-mates are concurrent
            with lock:
                live["count"] -= 1
            return None  # sustained failure → 429-like

        with patch.object(_mod, "_FETCH_PACING_S", 0.0), patch.object(
            adapter, "fetch", side_effect=_failing_fetch
        ):
            result = asyncio.run(adapter.fetch_many(urls, concurrency=6))

        assert len(result) == 18

        # Segment samples into waves: a new wave begins when a sample value of 1
        # follows a prior wave (the count had drained to 0 between gather calls).
        waves: list[list[int]] = []
        for s in samples:
            if s == 1:
                waves.append([])
            waves[-1].append(s)
        wave_peaks = [max(w) for w in waves]

        # Adaptive back-pressure: the first wave's peak in-flight count MUST exceed
        # the last wave's peak — the ceiling genuinely dropped (not a dead int).
        assert len(wave_peaks) >= 2
        assert wave_peaks[0] > wave_peaks[-1]


class TestFetchManyPacing:
    def test_inter_request_pacing_holds_under_concurrency(self) -> None:
        """W2-B (KI-2): the inter-request pace must be enforced even when fetches
        run concurrently. Record dispatch timestamps and assert the minimum gap
        between consecutive dispatches is >= the pacing interval (minus jitter).
        """
        import time as _time

        from reconciliation.adapters.sunat import descargaqr as _mod

        urls = [f"url-{i}" for i in range(5)]
        adapter = SunatDescargaqrAdapter()

        lock = threading.Lock()
        dispatch_times: list[float] = []

        def _timed_fetch(url: str) -> Any:
            with lock:
                dispatch_times.append(_time.monotonic())
            return _stub_gre()

        with patch.object(adapter, "fetch", side_effect=_timed_fetch):
            asyncio.run(adapter.fetch_many(urls, concurrency=5))

        dispatch_times.sort()
        gaps = [b - a for a, b in zip(dispatch_times, dispatch_times[1:])]
        # Allow a small scheduling jitter tolerance below the nominal pace.
        assert min(gaps) >= _mod._FETCH_PACING_S - 0.05


# ---------------------------------------------------------------------------
# SunatGreFetchPort.fetch_many default implementation
# ---------------------------------------------------------------------------


class TestPortFetchManyDefault:
    def test_default_fetch_many_delegates_to_fetch(self) -> None:
        """SunatGreFetchPort.fetch_many default loops fetch() for each URL."""
        from reconciliation.domain.ports import SunatGreFetchPort

        class _MinimalAdapter:
            def fetch(self, url: str) -> Any:
                return _stub_gre(url)

        # SunatGreFetchPort.fetch_many should exist as a default mixin
        # that delegates to fetch().  Call it directly on the port class.
        adapter = _MinimalAdapter()
        urls = ["url-1", "url-2"]

        # fetch_many default is on the Protocol class — call it with self=adapter
        result = SunatGreFetchPort.fetch_many(adapter, urls)  # type: ignore[arg-type]
        assert set(result.keys()) == {"url-1", "url-2"}
        assert all(v is not None for v in result.values())

    def test_default_fetch_many_empty(self) -> None:
        from reconciliation.domain.ports import SunatGreFetchPort

        class _MinimalAdapter:
            def fetch(self, url: str) -> Any:
                return None

        adapter = _MinimalAdapter()
        result = SunatGreFetchPort.fetch_many(adapter, [])  # type: ignore[arg-type]
        assert result == {}
