"""Unit tests for DiscardedPageResponse and ReconciliationTableResponse.discarded_pages.

STRICT TDD: tests MUST be RED before schemas.py is changed.
Tests fail because DiscardedPageResponse does not exist yet.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# 1.1.10 — ReconciliationTableResponse includes discarded_pages
# ---------------------------------------------------------------------------


class TestReconciliationTableResponseDiscardedPages:
    def test_reconciliation_table_response_includes_discarded_pages(self) -> None:
        """ReconciliationTableResponse must accept discarded_pages and round-trip correctly.

        Spec: REV-R33 / EXT-S033a, EXT-S033b.
        FAILS (RED): DiscardedPageResponse and field on table response don't exist yet.
        """
        from reconciliation.infrastructure.api.schemas import (  # type: ignore[attr-defined]
            DiscardedPageResponse,
            ReconciliationTableResponse,
        )

        table = ReconciliationTableResponse(
            run_id="test-run",
            rows=[],
            discarded_pages=[
                DiscardedPageResponse(page=152, registro="232", has_cached_lines=True)
            ],
        )

        assert len(table.discarded_pages) == 1
        entry = table.discarded_pages[0]
        assert entry.page == 152
        assert entry.registro == "232"
        assert entry.has_cached_lines is True

        # Verify round-trip serialization
        data = table.model_dump(mode="json")
        assert "discarded_pages" in data
        assert data["discarded_pages"][0]["page"] == 152

        # Verify re-validation from dict
        recovered = ReconciliationTableResponse.model_validate(data)
        assert recovered.discarded_pages[0].page == 152

    def test_discarded_pages_defaults_to_empty_list(self) -> None:
        """ReconciliationTableResponse.discarded_pages must default to [] so existing
        consumers that don't pass it are not broken.

        Spec: EXT-S033b (no breaking change to existing consumers).
        """
        from reconciliation.infrastructure.api.schemas import (  # type: ignore[attr-defined]
            ReconciliationTableResponse,
        )

        table = ReconciliationTableResponse(run_id="test-run", rows=[])
        assert table.discarded_pages == [], (
            "discarded_pages must default to [] (backward compat)"
        )


# ---------------------------------------------------------------------------
# 1.1.11 — DiscardedPageResponse is distinct from ErroredGuiaResponse
# ---------------------------------------------------------------------------


class TestDiscardedPageResponseDistinctFromErrored:
    def test_discarded_page_response_distinguishes_from_errored(self) -> None:
        """A ReconciliationTableResponse may carry both a DiscardedPageResponse
        (no-identity) and an ErroredGuiaResponse (valid identity, zero lines) —
        each must be in its own correct collection.

        DiscardedPageResponse MUST have a has_cached_lines boolean field
        (not present on ErroredGuiaResponse) to be distinguishable.

        Spec: REV-R33 / EXT-S033c.
        """
        from reconciliation.infrastructure.api.schemas import (  # type: ignore[attr-defined]
            DiscardedPageResponse,
            ErroredGuiaResponse,
            ReconciliationTableResponse,
        )

        discarded = DiscardedPageResponse(page=152, registro="232", has_cached_lines=False)
        errored = ErroredGuiaResponse(
            registro="232",
            guia_id="T001-0001",
            source_pages=[45],
        )

        table = ReconciliationTableResponse(
            run_id="test-run",
            rows=[],
            discarded_pages=[discarded],
            errored_guias=[errored],
        )

        # Correct collections
        assert len(table.discarded_pages) == 1
        assert len(table.errored_guias) == 1

        # Fields are distinct: DiscardedPageResponse has has_cached_lines; ErroredGuia does not
        assert hasattr(table.discarded_pages[0], "has_cached_lines"), (
            "DiscardedPageResponse must have has_cached_lines field"
        )
        assert not hasattr(table.errored_guias[0], "has_cached_lines"), (
            "ErroredGuiaResponse must NOT have has_cached_lines"
        )

        # DiscardedPageResponse does NOT have guia_id (it's page-keyed, not guia_id-keyed)
        assert not hasattr(table.discarded_pages[0], "guia_id"), (
            "DiscardedPageResponse must NOT have guia_id (it's page-keyed)"
        )
