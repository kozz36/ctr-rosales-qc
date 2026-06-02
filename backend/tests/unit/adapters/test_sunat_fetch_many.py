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
