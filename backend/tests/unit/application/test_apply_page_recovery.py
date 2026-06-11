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

import asyncio
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

    Sequential happy path: first recover removes the entry, second sees it gone.
    NOTE: this is the SEQUENTIAL invariant only. The double-count CRITICAL is
    proven by ``test_concurrent_recover_no_double_count`` below, which drives the
    REAL ReviewService under asyncio.gather (no manual list manipulation).
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
# CRITICAL (JD dual-blind, REPRODUCED) — concurrent double-count guard
# ---------------------------------------------------------------------------


def _build_real_reprocess_service_tier2(
    tmp_path,
    page: int = 88,
    registro: str | None = "232",
    cantidad: str = "2.500",
):
    """Wire a ReprocessService onto a REAL ReviewService driving the Tier-2 OCR path.

    Tier-2 is required to reproduce the TOCTOU: the executor await (render + OCR)
    runs AFTER the discarded-list lookup but BEFORE the commit lock — so two
    concurrent calls both pass the lookup, both suspend in run_in_executor, then
    both commit sequentially → double-append. (Tier-1 has no suspension between
    lookup and lock and would NOT reproduce the race.)
    """
    from reconciliation.application.reprocess_service import ReprocessService
    from reconciliation.application.review_service import ReviewService
    from reconciliation.application.run_context import RunContext

    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    output_base = tmp_path / "output"
    output_base.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(pdf_path=pdf_path, output_base=output_base, run_id="run_concurrent")

    # No cached lines → Tier-2 OCR path (the suspension window).
    dp = DiscardedPage(page=page, registro=registro, lines=[])

    review_service = ReviewService(
        declared=[],
        guias=[],
        rows=[],
        ctx=ctx,
        errored_guias=[],
        discarded_pages=[dp],
    )

    doc_source = MagicMock()
    doc_source.render_page.return_value = b"fake_image_bytes"
    identity = MagicMock()
    key_resolver = _make_key_resolver_mock()

    ocr_line = _make_line(cantidad=cantidad, source_page=page)
    extractor = MagicMock()
    extractor.extract_printed_table.return_value = [ocr_line]

    svc = ReprocessService(
        doc_source=doc_source,
        identity=identity,
        sunat=None,
        key_resolver=key_resolver,
        review_service=review_service,
        vision=None,
        extractor=extractor,
    )
    return svc, review_service, ctx


@pytest.mark.asyncio
async def test_concurrent_recover_no_double_count(tmp_path):
    """CRITICAL (JD ×2, Judge B reproduced) — two concurrent apply_page_recovery(88).

    The TOCTOU window: both callers' discarded-list lookup passes (entry still
    present), both build a guía, both suspend awaiting the commit lock, then both
    commit sequentially → ['recovered_88', 'recovered_88'], qty 2× (5.000 not
    2.500) AND TWO sidecar events → replay re-creates the corruption on EVERY
    restart.

    The fix is a lock-local idempotency/existence guard inside
    recover_discarded_page (page already gone → structured no-op).

    RED today: asserts will fail with TWO guías / doubled qty / two events.
    """
    svc, review_service, ctx = _build_real_reprocess_service_tier2(
        tmp_path, page=88, cantidad="2.500"
    )

    # Drive the REAL service concurrently — no manual list manipulation.
    results = await asyncio.gather(
        svc.apply_page_recovery(88),
        svc.apply_page_recovery(88),
    )

    # Exactly ONE recovered_88 guía in the ReviewService.
    recovered_guias = [g for g in review_service.guias if g.guia_id == "recovered_88"]
    assert len(recovered_guias) == 1, (
        f"Double-count: expected exactly 1 recovered_88 guía, got {len(recovered_guias)} "
        f"(guia_ids={[g.guia_id for g in review_service.guias]})"
    )

    # Quantity must be 1× (2.500) — NOT doubled to 5.000.
    total_qty = sum(
        (ln.cantidad for g in recovered_guias for ln in g.lines),
        Decimal("0"),
    )
    assert total_qty == Decimal("2.500"), (
        f"Quantity double-counted: expected 2.500, got {total_qty}"
    )

    # Exactly ONE recovered_discarded_page event written to the sidecar.
    sidecar = ctx.read_review_sidecar()
    events = [
        e for e in sidecar.get("edits", [])
        if e.get("kind") == "recovered_discarded_page"
    ]
    assert len(events) == 1, (
        f"Duplicate sidecar event: expected 1 recovered_discarded_page, got {len(events)}"
    )

    # Both calls return; exactly one should report recovered=True (the other a no-op).
    recovered_flags = [bool(r.recovered) for r in results]
    assert recovered_flags.count(True) == 1, (
        f"Exactly one call should recover; got recovered flags {recovered_flags}"
    )


# ---------------------------------------------------------------------------
# LOW (test gap) — requires_review coercion: cached line False/0.99 → True
# ---------------------------------------------------------------------------


def test_build_lines_coerces_requires_review_true_even_high_confidence():
    """LOW — _build_recovered_guia_lines_from_vision forces requires_review=True.

    A high-confidence cached line (requires_review=False, confidence=0.99) MUST
    be coerced to requires_review=True — recovered material is NEVER auto-accepted
    (reconciliation validation gate). Locks reprocess_service.py:247.
    """
    from reconciliation.application.reprocess_service import (
        _build_recovered_guia_lines_from_vision,
    )

    cached = _make_line(requires_review=False, confidence=0.99)
    kr = _make_key_resolver_mock()

    out = _build_recovered_guia_lines_from_vision(
        vision_lines=[cached], source_page=88, key_resolver=kr
    )

    assert len(out) == 1
    assert out[0].requires_review is True, (
        "Recovered line must be coerced requires_review=True regardless of confidence"
    )


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
