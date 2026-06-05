"""Tests for ReprocessService — transient retry, failure paths, and normalization parity.

Strict-TDD: all tests were written BEFORE the implementation (RED → GREEN).

Covers:
  T-2: _build_recovered_guia_lines parity (group_token/match_method/unit/cantidad
       from recovered lines == pipeline-shaped lines).
  T-5: apply_retry transient-success and failure paths (fake ports).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

import pytest

from reconciliation.domain.models import (
    ErroredGuia,
    GreLineItem,
    GuiaDeRemision,
    GuiaIdentity,
    MaterialLine,
    OfficialGre,
    ReconciliationRow,
    Registro,
)


# ---------------------------------------------------------------------------
# Fake ports (test doubles — NO heavy deps)
# ---------------------------------------------------------------------------


class _FakeDocSource:
    """Fake DocumentSourcePort — returns stub PNG bytes."""

    def __init__(self, pages: int = 5) -> None:
        self._pages = pages

    def page_count(self) -> int:
        return self._pages

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return b"FAKE_PNG"

    def page_text(self, idx: int) -> str | None:
        return None

    def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None:
        return None


class _FakeIdentitySuccess:
    """Fake IdentityExtractionPort — decode_identity returns None; decode_hashqr_url returns URL."""

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
        return None

    def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None:
        return "https://e-factura.sunat.gob.pe/v1/gre?hashqr=TOKEN123"


class _FakeIdentityNoUrl:
    """Fake IdentityExtractionPort — no hashqr URL found."""

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
        return None

    def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None:
        return None


class _FakeSunatSuccess:
    """Fake SunatGreFetchPort — returns an OfficialGre with 1 line."""

    GUIA_ID = "T009-0741770"
    FECHA_ENTREGA = date(2026, 5, 28)

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        return OfficialGre(
            guia_id=self.GUIA_ID,
            serie="T009",
            numero="0741770",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            fecha_entrega=self.FECHA_ENTREGA,
            lines=[
                GreLineItem(
                    cantidad=Decimal("4.124"),
                    unidad="TONELADAS",
                    descripcion='BARRA AG615/A706 G60 1/2" x 9M',
                )
            ],
        )


class _FakeSunatEmpty:
    """Fake SunatGreFetchPort — returns OfficialGre with no lines."""

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        return OfficialGre(
            guia_id="T009-0741770",
            serie="T009",
            numero="0741770",
            ruc_emisor="",
            ruc_receptor="",
            lines=[],
        )


class _FakeSunatNone:
    """Fake SunatGreFetchPort — returns None (network failure)."""

    def fetch(self, hashqr_url: str) -> OfficialGre | None:
        return None


class _FakeReviewService:
    """Minimal ReviewService stub for testing ReprocessService."""

    def __init__(self) -> None:
        self.recovered_guias: list[GuiaDeRemision] = []
        self.errored_guias: list[ErroredGuia] = [
            ErroredGuia(
                registro="232",
                guia_id="T009-0741770",
                source_pages=[4],
                retry_attempted=False,
            )
        ]
        self._rows: list[ReconciliationRow] = []

    @property
    def rows(self) -> list[ReconciliationRow]:
        return list(self._rows)

    def add_recovered_guia(self, guia: GuiaDeRemision) -> list[ReconciliationRow]:
        self.recovered_guias.append(guia)
        # Remove from errored_guias (idempotent)
        self.errored_guias = [e for e in self.errored_guias if e.guia_id != guia.guia_id]
        return list(self._rows)


# ---------------------------------------------------------------------------
# T-2: normalization parity test (CRUX — RED before reprocess_service exists)
# ---------------------------------------------------------------------------


class TestBuildRecoveredGuiaLinesParity:
    """T-2 parity test: lines from _build_recovered_guia_lines must match pipeline shape.

    The helper must produce MaterialLines with the SAME group_token (description_canonical)
    and match_method as the pipeline's _norm_line for the same raw description.
    """

    def test_lines_requires_review_always_true(self) -> None:
        """Recovered lines MUST have requires_review=True regardless of key.requires_review."""
        from reconciliation.application.reprocess_service import _build_recovered_guia_lines
        from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer
        from reconciliation.domain.material_key_resolver import MaterialKeyResolver

        key_resolver = MaterialKeyResolver(MaterialKeyNormalizer())
        official = _FakeSunatSuccess().fetch("url")
        assert official is not None

        lines = _build_recovered_guia_lines(
            official=official,
            source_page=4,
            key_resolver=key_resolver,
        )

        assert len(lines) > 0
        for line in lines:
            assert line.requires_review is True, (
                "Recovered lines MUST have requires_review=True (validation gate invariant)"
            )

    def test_parity_with_pipeline_norm_line(self) -> None:
        """group_token and match_method from _build_recovered_guia_lines must equal
        the pipeline's _norm_line output for the same description + unit.

        This is the CRUX correctness test for T-2.
        """
        from reconciliation.application.reprocess_service import _build_recovered_guia_lines
        from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer
        from reconciliation.domain.material_key_resolver import MaterialKeyResolver

        key_resolver = MaterialKeyResolver(MaterialKeyNormalizer())
        official = _FakeSunatSuccess().fetch("url")
        assert official is not None

        recovered_lines = _build_recovered_guia_lines(
            official=official,
            source_page=4,
            key_resolver=key_resolver,
        )

        # Build what the pipeline would produce via _norm_line
        from reconciliation.application.reprocess_service import _normalize_sunat_unit_for_recovery

        for gre_item, recovered_line in zip(official.lines, recovered_lines):
            normalized_unit = _normalize_sunat_unit_for_recovery(gre_item.unidad)
            if normalized_unit not in ("KG", "TN", "RD", "Rollo"):
                continue  # filtered out, skip
            # Pipeline _norm_line: key = key_resolver.resolve(description_raw, unidad)
            key = key_resolver.resolve(gre_item.descripcion, normalized_unit)  # type: ignore[arg-type]
            assert recovered_line.description_canonical == key.group_token, (
                f"description_canonical mismatch: "
                f"{recovered_line.description_canonical!r} != {key.group_token!r}"
            )
            assert recovered_line.match_method == key.method, (
                f"match_method mismatch: {recovered_line.match_method!r} != {key.method!r}"
            )
            assert recovered_line.unidad == normalized_unit, (
                f"unidad mismatch: {recovered_line.unidad!r} != {normalized_unit!r}"
            )
            assert recovered_line.cantidad == gre_item.cantidad

    def test_unknown_sunat_unit_filtered(self) -> None:
        """Lines with unmappable units are EXCLUDED from the result."""
        from reconciliation.application.reprocess_service import _build_recovered_guia_lines
        from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer
        from reconciliation.domain.material_key_resolver import MaterialKeyResolver

        key_resolver = MaterialKeyResolver(MaterialKeyNormalizer())
        official = OfficialGre(
            guia_id="T009-0741770",
            serie="T009",
            numero="0741770",
            ruc_emisor="",
            ruc_receptor="",
            lines=[
                GreLineItem(
                    cantidad=Decimal("1.0"),
                    unidad="UNKNOWN_UNIT",
                    descripcion="Some material",
                )
            ],
        )

        lines = _build_recovered_guia_lines(
            official=official,
            source_page=0,
            key_resolver=key_resolver,
        )

        assert lines == [], "Unknown units must be filtered out"

    def test_confidence_is_1_0(self) -> None:
        """SUNAT data is authoritative; confidence must always be 1.0."""
        from reconciliation.application.reprocess_service import _build_recovered_guia_lines
        from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer
        from reconciliation.domain.material_key_resolver import MaterialKeyResolver

        key_resolver = MaterialKeyResolver(MaterialKeyNormalizer())
        official = _FakeSunatSuccess().fetch("url")
        assert official is not None

        lines = _build_recovered_guia_lines(official=official, source_page=4, key_resolver=key_resolver)
        for line in lines:
            assert line.confidence == 1.0


# ---------------------------------------------------------------------------
# T-5: apply_retry transient-success and failure paths
# ---------------------------------------------------------------------------


class TestReprocessServiceApplyRetry:
    """T-5: ReprocessService.apply_retry transient-success and failure paths."""

    def _make_service(
        self,
        identity=None,
        sunat=None,
        review_service=None,
    ):
        from reconciliation.application.reprocess_service import ReprocessService
        from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer
        from reconciliation.domain.material_key_resolver import MaterialKeyResolver

        key_resolver = MaterialKeyResolver(MaterialKeyNormalizer())
        return ReprocessService(
            doc_source=_FakeDocSource(),
            identity=identity or _FakeIdentitySuccess(),
            sunat=sunat or _FakeSunatSuccess(),
            key_resolver=key_resolver,
            review_service=review_service or _FakeReviewService(),
        )

    def test_transient_success_returns_recovered_true(self) -> None:
        """When hashqr_url found + SUNAT returns lines → recovered=True."""
        review = _FakeReviewService()
        svc = self._make_service(review_service=review)

        result = svc.apply_retry(guia_id="T009-0741770", source_pages=[4])

        assert result.recovered is True

    def test_transient_success_calls_add_recovered_guia(self) -> None:
        """On success, ReviewService.add_recovered_guia must be called once."""
        review = _FakeReviewService()
        svc = self._make_service(review_service=review)

        svc.apply_retry(guia_id="T009-0741770", source_pages=[4])

        assert len(review.recovered_guias) == 1

    def test_transient_success_guia_has_requires_review_lines(self) -> None:
        """The recovered guía's lines must all have requires_review=True."""
        review = _FakeReviewService()
        svc = self._make_service(review_service=review)

        svc.apply_retry(guia_id="T009-0741770", source_pages=[4])

        assert len(review.recovered_guias) == 1
        guia = review.recovered_guias[0]
        assert all(line.requires_review for line in guia.lines)

    def test_transient_success_guia_fecha_is_fecha_entrega(self) -> None:
        """Recovered guía fecha must be the SUNAT fecha_entrega (R9b floor, no vision)."""
        review = _FakeReviewService()
        svc = self._make_service(review_service=review)

        svc.apply_retry(guia_id="T009-0741770", source_pages=[4])

        guia = review.recovered_guias[0]
        # fecha == SUNAT fecha_entrega (apply_delivery_floor(None, fecha_entrega) → fecha_entrega)
        assert guia.fecha == _FakeSunatSuccess.FECHA_ENTREGA

    def test_no_hashqr_url_returns_recovered_false(self) -> None:
        """When no hashqr_url is decoded → recovered=False, guía stays errored."""
        review = _FakeReviewService()
        svc = self._make_service(identity=_FakeIdentityNoUrl(), review_service=review)

        result = svc.apply_retry(guia_id="T009-0741770", source_pages=[4])

        assert result.recovered is False
        assert result.reason == "no_hashqr_url"

    def test_no_hashqr_url_no_garbage_guia_added(self) -> None:
        """On failure: no GuiaDeRemision must be added to ReviewService."""
        review = _FakeReviewService()
        svc = self._make_service(identity=_FakeIdentityNoUrl(), review_service=review)

        svc.apply_retry(guia_id="T009-0741770", source_pages=[4])

        assert len(review.recovered_guias) == 0

    def test_sunat_empty_returns_recovered_false(self) -> None:
        """When SUNAT returns 0 lines → recovered=False, reason=sunat_empty."""
        review = _FakeReviewService()
        svc = self._make_service(sunat=_FakeSunatEmpty(), review_service=review)

        result = svc.apply_retry(guia_id="T009-0741770", source_pages=[4])

        assert result.recovered is False
        assert result.reason == "sunat_empty"

    def test_sunat_none_returns_recovered_false(self) -> None:
        """When SUNAT returns None (network failure) → recovered=False."""
        review = _FakeReviewService()
        svc = self._make_service(sunat=_FakeSunatNone(), review_service=review)

        result = svc.apply_retry(guia_id="T009-0741770", source_pages=[4])

        assert result.recovered is False
