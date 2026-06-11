"""Tests for ReprocessService.apply_page_recovery (PR-2 — EXT-036/037).

Strict TDD: all tests written first (RED), then GREEN implementation.

Tier selection invariants:
  Tier 1 — cached lines non-empty → zero render/OCR/vision calls.
  Tier 2 — cached lines empty → OCR called; vision NOT called.
  Tier 3 — cached lines empty, OCR returns [] → vision called.
  All empty → PageRecoveryResult(recovered=False, reason="empty"); entry STAYS.

recovered guía invariants:
  - guia_id = f"recovered_{page}" (deterministic, collision-free with QR format).
  - identity_source = "operator".
  - registro inherited from DiscardedPage.registro (no dialog/parameter).
  - ALL lines requires_review=True unconditionally.
  - Double-recover is idempotent (second call: not_found).
  - vision-off (NullVisionAdapter) + OCR empty → structured failure, not 503.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reconciliation.domain.models import (
    DiscardedPage,
    GuiaDeRemision,
    MaterialLine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_line(
    description_raw: str = "BARRA A615 G60 1/2\"",
    description_canonical: str = "BARRA A615 G60 1/2\" 9M",
    cantidad: str = "1.000",
    unidad: str = "TN",
    source_page: int = 152,
    requires_review: bool = True,
    confidence: float = 0.95,
) -> MaterialLine:
    return MaterialLine(
        description_raw=description_raw,
        description_canonical=description_canonical,
        cantidad=Decimal(cantidad),
        unidad=unidad,
        source_page=source_page,
        requires_review=requires_review,
        confidence=confidence,
        match_method="deterministic",
    )


def _make_discarded_page(
    page: int = 152,
    registro: str | None = "232",
    lines: list[MaterialLine] | None = None,
) -> DiscardedPage:
    return DiscardedPage(
        page=page,
        registro=registro,
        lines=lines or [],
    )


def _make_review_service_mock(discarded_pages: list[DiscardedPage] | None = None):
    """Return a mock ReviewService with discarded_pages property."""
    rs = MagicMock()
    rs.discarded_pages = list(discarded_pages or [])
    rs.recover_discarded_page = MagicMock(return_value=[])
    return rs


def _make_key_resolver_mock():
    """Return a key_resolver that passes through description_canonical unchanged."""
    from unittest.mock import MagicMock

    class _FakeKey:
        group_token = "BARRA A615 G60 1/2\" 9M"
        method = "deterministic"

    kr = MagicMock()
    kr.resolve.return_value = _FakeKey()
    return kr


def _build_reprocess_service(
    discarded_pages: list[DiscardedPage] | None = None,
    ocr_lines: list[MaterialLine] | None = None,
    vision_lines: list[MaterialLine] | None = None,
    vision_enabled: bool = True,
    extractor=None,
):
    """Build a ReprocessService with mocked ports for testing apply_page_recovery."""
    from reconciliation.application.reprocess_service import ReprocessService

    review_service = _make_review_service_mock(discarded_pages)

    # doc_source mock
    doc_source = MagicMock()
    doc_source.render_page.return_value = b"fake_image_bytes"

    # identity mock
    identity = MagicMock()

    # key_resolver
    key_resolver = _make_key_resolver_mock()

    # vision mock
    if vision_enabled:
        vision = MagicMock()
        vision.read_material_table.return_value = vision_lines or []
    else:
        from reconciliation.adapters.vision.null_vision import NullVisionAdapter
        vision = NullVisionAdapter()

    # extractor (OCR port)
    if extractor is None:
        extractor_mock = MagicMock()
        extractor_mock.extract_printed_table.return_value = ocr_lines or []
    else:
        extractor_mock = extractor

    svc = ReprocessService(
        doc_source=doc_source,
        identity=identity,
        sunat=None,
        key_resolver=key_resolver,
        review_service=review_service,
        vision=vision,
        extractor=extractor_mock,
    )
    return svc, review_service, doc_source, extractor_mock, vision if vision_enabled else None


# ---------------------------------------------------------------------------
# 2.1.1 — Tier 1: cached lines → zero OCR/vision calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_cached_lines_no_ocr_no_vision_called():
    """EXT-036 / EXT-S036a — Tier 1: cached lines non-empty → no OCR, no vision.

    FAILS today: apply_page_recovery does not exist.
    """
    cached_line = _make_line()
    page = _make_discarded_page(page=152, lines=[cached_line])

    svc, review_service, doc_source, extractor_mock, vision_mock = _build_reprocess_service(
        discarded_pages=[page],
        vision_enabled=True,
    )

    result = await svc.apply_page_recovery(152)

    assert result.recovered is True
    assert result.page == 152
    extractor_mock.extract_printed_table.assert_not_called()
    doc_source.render_page.assert_not_called()
    # vision.read_material_table should NOT be called for Tier-1
    if vision_mock is not None:
        vision_mock.read_material_table.assert_not_called()
    # recovered guia committed
    review_service.recover_discarded_page.assert_called_once()


# ---------------------------------------------------------------------------
# 2.1.2 — Tier 2: empty cached lines → OCR called, vision NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier2_empty_cached_lines_ocr_called():
    """EXT-036 / EXT-S036b — empty cached lines → OCR called, vision NOT called.

    FAILS today: apply_page_recovery does not exist.
    """
    ocr_line = _make_line(description_raw="BARRA A615 G60 3/4\"", source_page=57)
    page = _make_discarded_page(page=57, lines=[])

    svc, review_service, doc_source, extractor_mock, vision_mock = _build_reprocess_service(
        discarded_pages=[page],
        ocr_lines=[ocr_line],
        vision_enabled=True,
    )

    result = await svc.apply_page_recovery(57)

    assert result.recovered is True
    extractor_mock.extract_printed_table.assert_called_once()
    if vision_mock is not None:
        vision_mock.read_material_table.assert_not_called()
    review_service.recover_discarded_page.assert_called_once()


# ---------------------------------------------------------------------------
# 2.1.3 — Tier 3: empty cached lines + OCR returns [] → vision called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier3_empty_ocr_vision_fallback():
    """EXT-036 / EXT-S036c — empty lines + empty OCR → vision fallback called.

    FAILS today: apply_page_recovery does not exist.
    """
    vision_line = _make_line(source_page=99)
    page = _make_discarded_page(page=99, lines=[])

    svc, review_service, doc_source, extractor_mock, vision_mock = _build_reprocess_service(
        discarded_pages=[page],
        ocr_lines=[],       # OCR returns nothing → falls to Tier 3
        vision_lines=[vision_line],
        vision_enabled=True,
    )

    result = await svc.apply_page_recovery(99)

    assert result.recovered is True
    extractor_mock.extract_printed_table.assert_called_once()
    assert vision_mock is not None
    vision_mock.read_material_table.assert_called_once()


# ---------------------------------------------------------------------------
# 2.1.4 — All tiers empty → recovered=False, entry STAYS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tiers_empty_recovery_fails_entry_retained():
    """EXT-036 / EXT-S036c — all tiers return [] → recovered=False; entry retained.

    REV-R30-S04: failed entries must NOT be removed.
    FAILS today: apply_page_recovery does not exist.
    """
    page = _make_discarded_page(page=165, lines=[])

    svc, review_service, doc_source, extractor_mock, vision_mock = _build_reprocess_service(
        discarded_pages=[page],
        ocr_lines=[],
        vision_lines=[],
        vision_enabled=True,
    )

    result = await svc.apply_page_recovery(165)

    assert result.recovered is False
    assert result.reason == "empty"
    review_service.recover_discarded_page.assert_not_called()


# ---------------------------------------------------------------------------
# 2.1.5 — ALL recovered lines require_review unconditionally (high-confidence OCR)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_recovered_lines_require_review_unconditionally():
    """EXT-037 / EXT-S037b — requires_review=True on all lines regardless of confidence.

    REV-R30-S03: absolute invariant — no auto-accept even at confidence 1.0.
    FAILS today: apply_page_recovery does not exist.
    """
    # Use Tier-1 path: cached line with high confidence but requires_review=True already
    # (the normalization in _build_recovered_guia_lines_from_vision forces it)
    cached_line = _make_line(confidence=0.99, requires_review=True)
    page = _make_discarded_page(page=152, lines=[cached_line])

    svc, review_service, doc_source, extractor_mock, vision_mock = _build_reprocess_service(
        discarded_pages=[page],
    )

    result = await svc.apply_page_recovery(152)

    assert result.recovered is True
    # Check that recover_discarded_page was called with a guia whose lines all have requires_review=True
    call_args = review_service.recover_discarded_page.call_args
    guia: GuiaDeRemision = call_args[1]["guia"] if "guia" in call_args[1] else call_args[0][1]
    assert all(ln.requires_review is True for ln in guia.lines), (
        "Every recovered line MUST have requires_review=True (reconciliation gate)"
    )


# ---------------------------------------------------------------------------
# 2.1.6 — recovered guia_id format + identity_source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovered_guia_id_format_no_collision_with_qr():
    """EXT-037 / EXT-S037a — guia_id='recovered_{page}'; identity_source='operator'.

    FAILS today: apply_page_recovery does not exist.
    """
    import re

    cached_line = _make_line()
    page = _make_discarded_page(page=152, lines=[cached_line])

    svc, review_service, doc_source, extractor_mock, vision_mock = _build_reprocess_service(
        discarded_pages=[page],
    )

    result = await svc.apply_page_recovery(152)

    assert result.recovered is True
    assert result.guia_id == "recovered_152"

    # QR format is serie-numero e.g. "T009-0741770"
    qr_pattern = re.compile(r"^[A-Z]\d+-\d+$")
    assert not qr_pattern.match(result.guia_id), "guia_id must NOT match QR format"

    # Verify identity_source on the committed guia
    call_args = review_service.recover_discarded_page.call_args
    guia: GuiaDeRemision = call_args[1]["guia"] if "guia" in call_args[1] else call_args[0][1]
    assert guia.identity_source == "operator"


# ---------------------------------------------------------------------------
# 2.1.7 — registro inherited from DiscardedPage (no dialog/parameter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovered_guia_inherits_section_registro():
    """EXT-037 / EXT-S037c — guia.registro == entry.registro; no assignment dialog.

    REV-R31-S05.
    FAILS today: apply_page_recovery does not exist.
    """
    cached_line = _make_line()
    page = _make_discarded_page(page=152, registro="232", lines=[cached_line])

    svc, review_service, doc_source, extractor_mock, vision_mock = _build_reprocess_service(
        discarded_pages=[page],
    )

    result = await svc.apply_page_recovery(152)

    assert result.recovered is True
    call_args = review_service.recover_discarded_page.call_args
    guia: GuiaDeRemision = call_args[1]["guia"] if "guia" in call_args[1] else call_args[0][1]
    assert guia.registro == "232"


# ---------------------------------------------------------------------------
# 2.1.8 — Double-recover is idempotent (second call: not_found)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_recover_idempotent():
    """Design §2 — second recover attempt returns not_found (entry already removed).

    FAILS today: apply_page_recovery does not exist.
    """
    cached_line = _make_line()
    page = _make_discarded_page(page=152, lines=[cached_line])

    svc, review_service, doc_source, extractor_mock, vision_mock = _build_reprocess_service(
        discarded_pages=[page],
    )

    # First call succeeds and the mock review_service.recover_discarded_page is called
    result1 = await svc.apply_page_recovery(152)
    assert result1.recovered is True

    # Second call: page 152 no longer in discarded list (mocked review_service
    # doesn't actually mutate svc._review_service.discarded_pages in our mock,
    # but the service reads .discarded_pages on each call so we simulate removal)
    review_service.discarded_pages = []  # simulate entry removed after first recovery
    result2 = await svc.apply_page_recovery(152)

    assert result2.recovered is False
    assert result2.reason == "not_found"
    # Only ONE commit (from the first recovery)
    assert review_service.recover_discarded_page.call_count == 1


# ---------------------------------------------------------------------------
# 2.1.18 — Vision-off + OCR empty → structured failure, NOT 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vision_off_ocr_still_attempted_failure_not_503():
    """REV-R31-S04 — NullVisionAdapter active + OCR returns [] → structured failure.

    The response must be a PageRecoveryResult(recovered=False), NOT a 503 or crash.
    Entry remains in discarded_pages.
    FAILS today: apply_page_recovery does not exist.
    """
    page = _make_discarded_page(page=279, lines=[])

    svc, review_service, doc_source, extractor_mock, _vision = _build_reprocess_service(
        discarded_pages=[page],
        ocr_lines=[],
        vision_enabled=False,   # NullVisionAdapter injected
    )

    # Must not raise — structured failure only
    result = await svc.apply_page_recovery(279)

    assert result.recovered is False
    assert result.reason in ("empty",)  # structured reason, not 503
    review_service.recover_discarded_page.assert_not_called()
