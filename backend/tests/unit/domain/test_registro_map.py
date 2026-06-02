"""Unit tests for _derive_numero / build_page_to_registro_map UNRESOLVED fix (S1.4).

Spec refs: EXT-018, EXT-S19, EXT-S20, REC-C05, REC-C07, design §E.

Key assertions:
- Section IDs (e.g. "4252") are NEVER emitted as registro numeros.
- Derivation failure → None in the map, not the Contents ID.
- Valid derivation → correct Registro N° string.
- Pages with registro=None appear in unresolved_guias (structural test).
"""

from __future__ import annotations

from reconciliation.domain.models import GuiaDeRemision, Registro
from reconciliation.domain.section_id_guard import is_section_id
from reconciliation.infrastructure.container import (
    _derive_numero,
    build_page_to_registro_map,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDoc:
    def __init__(self, pages: dict[int, str | None]) -> None:
        self._pages = pages

    def page_count(self) -> int:
        return max(self._pages) + 1 if self._pages else 0

    def page_text(self, idx: int) -> str | None:
        return self._pages.get(idx)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return b"\x89PNG"


class FakeExtractor:
    def __init__(
        self,
        proto_map: dict[int, Registro],
        detail_map: dict[int, Registro] | None = None,
    ) -> None:
        self._proto = proto_map
        self._detail = detail_map or {}

    def extract_registro_from_proto_page(self, text: str, source_page: int) -> Registro | None:
        return self._proto.get(source_page)

    def extract_registro_from_detail_page(self, text: str, source_page: int) -> Registro | None:
        return self._detail.get(source_page)


def _reg(numero: str) -> Registro:
    return Registro(numero=numero, fecha_declarada=None, declared_lines=[])


# ---------------------------------------------------------------------------
# _derive_numero tests
# ---------------------------------------------------------------------------


class TestDeriveNumero:
    """Unit tests for _derive_numero — the core derivation function."""

    def test_proto_page_yields_registro_numero(self) -> None:
        doc = FakeDoc({2: "PROTOCOLO DE RECEPCI\ntext"})
        extractor = FakeExtractor(proto_map={2: _reg("232")})
        result = _derive_numero(
            "4252", start_0=2, end_0=6, doc_source=doc, declared_extractor=extractor
        )
        assert result == "232"

    def test_detail_page_yields_registro_numero_when_no_proto(self) -> None:
        doc = FakeDoc({2: "Form detail\ntext"})
        extractor = FakeExtractor(proto_map={}, detail_map={2: _reg("231")})
        result = _derive_numero(
            "4251", start_0=2, end_0=6, doc_source=doc, declared_extractor=extractor
        )
        assert result == "231"

    def test_no_declared_page_returns_none(self) -> None:
        """EXT-018: when no DECLARED page is found, return None (never the Contents ID)."""
        doc = FakeDoc({2: None, 3: "GUIA DE REMISION\ntext"})
        extractor = FakeExtractor(proto_map={}, detail_map={})
        result = _derive_numero(
            "4252", start_0=2, end_0=6, doc_source=doc, declared_extractor=extractor
        )
        assert result is None

    def test_section_id_as_contents_id_not_returned_on_failure(self) -> None:
        """EXT-S20 regression guard: result must not be a section ID."""
        doc = FakeDoc({0: None})
        extractor = FakeExtractor(proto_map={}, detail_map={})
        result = _derive_numero(
            "4252", start_0=0, end_0=3, doc_source=doc, declared_extractor=extractor
        )
        # Key invariant: if result is not None, it must not be a section ID
        if result is not None:
            assert not is_section_id(result), (
                f"_derive_numero returned section ID {result!r} — EXT-018 violation"
            )
        # In this case, derivation fails → None expected
        assert result is None

    def test_proto_numero_that_is_section_id_returns_none(self) -> None:
        """If a derived proto_numero itself matches the section-ID pattern, return None."""
        # Edge case: a pathological parser returns a 4xxx value from a PROTO page.
        doc = FakeDoc({2: "PROTOCOLO DE RECEPCI\ntext"})
        extractor = FakeExtractor(proto_map={2: _reg("4252")})  # pathological
        result = _derive_numero(
            "4252", start_0=2, end_0=6, doc_source=doc, declared_extractor=extractor
        )
        assert result is None


# ---------------------------------------------------------------------------
# build_page_to_registro_map tests (EXT-018 focus)
# ---------------------------------------------------------------------------


class TestBuildPageToRegistroMapUnresolved:
    """Focused tests on UNRESOLVED behaviour after EXT-018 fix."""

    def test_section_id_input_returns_none_in_map(self) -> None:
        """EXT-S20: section ID '4252' MUST NEVER appear in the map as a value."""
        result = build_page_to_registro_map({"4252": 1}, total_pages=3)
        for page, value in result.items():
            assert value is None, (
                f"page {page}: section ID '4252' must map to None, got {value!r}"
            )

    def test_non_section_id_without_doc_source_preserved(self) -> None:
        """Contents IDs that are NOT section IDs are preserved when doc_source is absent."""
        # "A" is not a section ID → preserved as-is in legacy mode
        result = build_page_to_registro_map({"A": 1}, total_pages=5)
        assert result[0] == "A"
        assert result[1] == "A"

    def test_valid_derivation_yields_correct_numero(self) -> None:
        """Happy path: derivation succeeds → correct Registro N° in map."""
        doc = FakeDoc({2: "PROTOCOLO DE RECEPCI\ntext"})
        extractor = FakeExtractor(proto_map={2: _reg("232")})
        result = build_page_to_registro_map(
            {"4252": 3},
            total_pages=6,
            doc_source=doc,
            declared_extractor=extractor,
        )
        for page in range(2, 6):
            assert result[page] == "232", f"page {page}: expected '232', got {result[page]!r}"

    def test_failed_derivation_yields_none_in_map(self) -> None:
        """EXT-S19: derivation failure → None in map, not Contents ID."""
        doc = FakeDoc({2: None, 3: "GUIA DE REMISION\ntext"})
        extractor = FakeExtractor(proto_map={}, detail_map={})
        result = build_page_to_registro_map(
            {"4252": 3},
            total_pages=6,
            doc_source=doc,
            declared_extractor=extractor,
        )
        for page in range(2, 6):
            assert result[page] is None

    def test_no_map_value_is_section_id(self) -> None:
        """Regression guard: iterating all values — none should match is_section_id."""
        doc = FakeDoc({
            0: "PROTOCOLO DE RECEPCI\ntext",
            5: "PROTOCOLO DE RECEPCI\ntext",
        })
        extractor = FakeExtractor(
            proto_map={0: _reg("232"), 5: _reg("231")},
        )
        result = build_page_to_registro_map(
            {"4252": 1, "4251": 6},
            total_pages=10,
            doc_source=doc,
            declared_extractor=extractor,
        )
        for page, value in result.items():
            if value is not None:
                assert not is_section_id(value), (
                    f"page {page}: value {value!r} is a section ID — EXT-018 violation"
                )


# ---------------------------------------------------------------------------
# Structural test: ReconciliationResult.unresolved_guias (REC-C05)
# ---------------------------------------------------------------------------


class TestUnresolvedGuias:
    """Verify that GuiaDeRemision with registro=None routes to unresolved_guias."""

    def test_guia_with_none_registro_is_unresolved(self) -> None:
        """A GuiaDeRemision with registro=None must be treatable as unresolved."""
        from reconciliation.domain.models import ReconciliationResult

        guia = GuiaDeRemision(
            guia_id="T009-0741770",
            registro=None,
            fecha=None,
            lines=[],
            source_pages=[5],
        )
        result = ReconciliationResult(rows=[], unresolved_guias=[guia])
        assert len(result.unresolved_guias) == 1
        assert result.unresolved_guias[0].registro is None

    def test_reconciliation_result_default_empty_unresolved(self) -> None:
        from reconciliation.domain.models import ReconciliationResult

        result = ReconciliationResult(rows=[])
        assert result.unresolved_guias == []

    def test_4252_as_registro_is_detected_as_section_id(self) -> None:
        """EXT-S20 regression guard: any guía with registro='4252' is invalid."""
        # This test documents the invariant: if registro is "4252", it violates EXT-018.
        # GuiaDeRemision construction itself allows it (the field is str | None),
        # but the guard must catch it at derivation time (tested above).
        guia = GuiaDeRemision(
            guia_id="G-001",
            registro="4252",
            fecha=None,
            lines=[],
            source_pages=[3],
        )
        # The section-ID predicate identifies this as a content ID, never a registro
        assert is_section_id(guia.registro) is True, (  # type: ignore[arg-type]
            "'4252' must be identified as a section ID by the predicate"
        )
