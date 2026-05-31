"""Unit tests for PageClassifier (task 1.2 + real-PDF hardening).

Covers:
- Real-PDF universal header noise: title NOT on first line.
- Cover vs. detail disambiguation.
- Contents page disambiguation.
- Protocolo takes priority over embedded GUIA field label.
- Scanned pages (header-only) → ocr_title path → GUIA / UNCLASSIFIED.
- Legacy direct-title tests (still valid for non-Autodesk PDFs).
- Case-insensitive / unicode normalisation.
- Supplier-name-only text → UNCLASSIFIED (EXT-001).
- LOW_CONFIDENCE_THRESHOLD invariant (EXT-002).
"""

from __future__ import annotations

import pytest

from reconciliation.domain.classifier import LOW_CONFIDENCE_THRESHOLD, PageClassifier

# ---------------------------------------------------------------------------
# Shared header noise present on every page of the real PDF
# ---------------------------------------------------------------------------

_UNIVERSAL_HEADER = (
    "PTR001-TORRE ROSALES\n"
    "Informe de detalle del formulario\n"
)

_UNIVERSAL_FOOTER = (
    "Created by Sandra Sopla Pinedo with Autodesk® Forma® on May 31, 2026 at 11:56 AM UTC-05:00\n"
    "Page 3 of 493\n"
)


def _with_noise(body: str) -> str:
    """Wrap body text with the real universal header + footer noise."""
    return _UNIVERSAL_HEADER + body + _UNIVERSAL_FOOTER


@pytest.fixture()
def clf() -> PageClassifier:
    return PageClassifier()


# ---------------------------------------------------------------------------
# Real-PDF: noise stripping — title buried below universal header
# ---------------------------------------------------------------------------

class TestRealPdfNoiseStripping:
    """The classifier must scan the whole body, not just the first line."""

    def test_cover_page_noise_stripped(self, clf: PageClassifier) -> None:
        # Page 1: header noise + cover metadata — no record marker
        text = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "Form detail\n"
            "Informe de detalle del formulario\n"
            "Created on\n"
            "May 31, 2026, 11:56 AM UTC-05:00\n"
            "Created by\n"
            "Sandra Sopla Pinedo (Consorcio Torre Rosales)\n"
            "Total items\n"
            "11\n"
            "Sorted by\n"
            "Form date (Descending)\n"
            "Filtered by\n"
            "Status (In progress)\n"
        )
        result = clf.classify(text)
        assert result.kind == "IGNORED"
        assert result.title_matched == "COVER"

    def test_contents_page_noise_stripped(self, clf: PageClassifier) -> None:
        # Page 2: header noise + Contents TOC
        text = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "Contents\n"
            "#4252: CTR-PLC01-FR001_RECEPCION DE MATERIALES EN OBRA .... 3\n"
            "#4251: CTR-PLC01-FR001_RECEPCION DE MATERIALES EN OBRA .... 25\n"
            "Created by Sandra Sopla Pinedo with Autodesk® Forma® on May 31, 2026\n"
            "Page 2 of 493\n"
        )
        result = clf.classify(text)
        assert result.kind == "IGNORED"
        assert result.title_matched == "CONTENTS"

    def test_detail_page_noise_stripped(self, clf: PageClassifier) -> None:
        # Page 3: header noise + Form detail + record marker + Description + Notes
        text = _with_noise(
            "Form detail\n"
            "#4252: CTR-PLC01-FR001_RECEPCION DE MATERIALES EN OBRA\n"
            "Forms\n"
            "Location\n"
            "VARIOS NIVELES\n"
            "Description\n"
            "232\n"
            "Notes\n"
            "BARRA A615/A706 G60 8MM (DOB) - 2.04 TN\n"
        )
        result = clf.classify(text)
        assert result.kind == "DECLARED"
        assert result.title_matched == "FORM DETAIL"

    def test_protocolo_page_noise_stripped(self, clf: PageClassifier) -> None:
        # Page 4: header noise + protocolo body (title buried at line ~75)
        text = _with_noise(
            "Código:\n"
            "Página\n"
            ":\n"
            "1\n"
            "de\n"
            "1\n"
            "PROYECTO\n"
            ":\n"
            "EDIFICIO TORRE ROSALES\n"
            "GUIA DE REMISIÓN\n"   # field label — must NOT trigger GUIA
            "NO\n"
            "PROTOCOLO DE RECEPCIÓN\n"
            "Rev: 02\n"
            "RECEPCION DE MATERIALES EN OBRA\n"
        )
        result = clf.classify(text)
        assert result.kind == "DECLARED"
        assert result.title_matched == "PROTOCOLO DE RECEPCION"

    def test_scanned_page_header_only_without_ocr_unclassified(
        self, clf: PageClassifier
    ) -> None:
        # Pages 5-N: only the 4-line noise overlay, no body content
        text = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "Created by Sandra Sopla Pinedo with Autodesk® Forma® on May 31, 2026\n"
            "Page 5 of 493\n"
        )
        result = clf.classify(text, ocr_title=None)
        assert result.kind == "UNCLASSIFIED"
        assert result.title_matched is None

    def test_scanned_page_with_ocr_guia(self, clf: PageClassifier) -> None:
        # Scanned guía page: header-only page_text + ocr_title carries the real title
        text = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "Created by Sandra Sopla Pinedo with Autodesk® Forma® on May 31, 2026\n"
            "Page 5 of 493\n"
        )
        result = clf.classify(text, ocr_title="GUIA DE REMISION")
        assert result.kind == "GUIA"
        assert result.title_matched == "GUIA DE REMISION"

    def test_scanned_page_with_ocr_guia_unicode(self, clf: PageClassifier) -> None:
        text = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "Created by Sandra Sopla Pinedo with Autodesk® Forma®\n"
            "Page 6 of 493\n"
        )
        result = clf.classify(text, ocr_title="GUÍA DE REMISIÓN")
        assert result.kind == "GUIA"


# ---------------------------------------------------------------------------
# Cover vs. detail disambiguation
# ---------------------------------------------------------------------------

class TestCoverDetailDisambiguation:
    """Cover page MUST NOT be classified as DECLARED despite having 'Form detail' text."""

    def test_cover_has_form_detail_text_but_no_record_marker(
        self, clf: PageClassifier
    ) -> None:
        # Cover contains "Form detail" (repeated header noise) but NO #digits: marker
        # and has cover metadata (Total items) — must be IGNORED
        text = _with_noise(
            "Form detail\n"
            "Informe de detalle del formulario\n"
            "Total items\n"
            "11\n"
            "Sorted by\n"
            "Form date (Descending)\n"
        )
        result = clf.classify(text)
        assert result.kind == "IGNORED"

    def test_detail_requires_record_marker_or_description_notes(
        self, clf: PageClassifier
    ) -> None:
        # "Form detail" alone (after noise strip) with no record marker is UNCLASSIFIED
        text = _with_noise("Form detail\n")
        result = clf.classify(text)
        # No record marker, no Description+Notes → UNCLASSIFIED
        assert result.kind == "UNCLASSIFIED"

    def test_detail_with_only_record_marker_is_declared(
        self, clf: PageClassifier
    ) -> None:
        text = _with_noise(
            "Form detail\n"
            "#4252: CTR-PLC01-FR001_RECEPCION DE MATERIALES EN OBRA\n"
        )
        result = clf.classify(text)
        assert result.kind == "DECLARED"

    def test_detail_with_description_and_notes_is_declared(
        self, clf: PageClassifier
    ) -> None:
        text = _with_noise(
            "Form detail\n"
            "Description\n"
            "232\n"
            "Notes\n"
            "some material notes\n"
        )
        result = clf.classify(text)
        assert result.kind == "DECLARED"


# ---------------------------------------------------------------------------
# Protocolo takes priority over embedded GUIA field label
# ---------------------------------------------------------------------------

class TestProtocoloPriorityOverGuia:
    def test_protocolo_wins_when_guia_present_as_field_label(
        self, clf: PageClassifier
    ) -> None:
        # Real protocolo page contains "GUIA DE REMISION" as a form field.
        # PROTOCOLO must win.
        text = _with_noise(
            "GUIA DE REMISIÓN\n"
            "NO\n"
            "PROTOCOLO DE RECEPCIÓN\n"
            "Rev: 02\n"
        )
        result = clf.classify(text)
        assert result.kind == "DECLARED"
        assert result.title_matched == "PROTOCOLO DE RECEPCION"

    def test_protocolo_ascii_variant(self, clf: PageClassifier) -> None:
        text = _with_noise("PROTOCOLO DE RECEPCION\nContenido\n")
        result = clf.classify(text)
        assert result.kind == "DECLARED"
        assert result.title_matched == "PROTOCOLO DE RECEPCION"

    def test_protocolo_unicode_variant(self, clf: PageClassifier) -> None:
        text = _with_noise("PROTOCOLO DE RECEPCIÓN\nContenido\n")
        result = clf.classify(text)
        assert result.kind == "DECLARED"


# ---------------------------------------------------------------------------
# Legacy direct-title tests (non-Autodesk PDFs; first-line title still works)
# ---------------------------------------------------------------------------

class TestKindMappings:
    def test_guia_canonical_unicode(self, clf: PageClassifier) -> None:
        result = clf.classify("GUÍA DE REMISIÓN\nAlgún contenido")
        assert result.kind == "GUIA"
        assert result.title_matched is not None

    def test_guia_ascii_fallback(self, clf: PageClassifier) -> None:
        result = clf.classify("GUIA DE REMISION\nOtro contenido")
        assert result.kind == "GUIA"

    def test_planilla_resumen_ignored(self, clf: PageClassifier) -> None:
        result = clf.classify(
            "Sistema de Gestión de la Calidad - Planilla Resumen\nAlgo"
        )
        assert result.kind == "IGNORED"

    def test_planilla_resumen_short_form(self, clf: PageClassifier) -> None:
        result = clf.classify("PLANILLA RESUMEN")
        assert result.kind == "IGNORED"

    def test_listado_de_barras_ignored(self, clf: PageClassifier) -> None:
        result = clf.classify("LISTADO DE BARRAS\nalgo")
        assert result.kind == "IGNORED"

    def test_listado_de_barras_full_form(self, clf: PageClassifier) -> None:
        result = clf.classify(
            "Sistema de Gestión de la Calidad - Listado de Barras\n"
        )
        assert result.kind == "IGNORED"

    def test_protocolo_de_recepcion_declared(self, clf: PageClassifier) -> None:
        result = clf.classify("PROTOCOLO DE RECEPCIÓN\nContenido")
        assert result.kind == "DECLARED"

    def test_protocolo_ascii(self, clf: PageClassifier) -> None:
        result = clf.classify("PROTOCOLO DE RECEPCION\nContenido")
        assert result.kind == "DECLARED"

    def test_caratula_ignored(self, clf: PageClassifier) -> None:
        result = clf.classify("CARÁTULA\nContenido")
        assert result.kind == "IGNORED"

    def test_caratula_ascii(self, clf: PageClassifier) -> None:
        result = clf.classify("CARATULA")
        assert result.kind == "IGNORED"


# ---------------------------------------------------------------------------
# Case-insensitive / unicode normalisation
# ---------------------------------------------------------------------------

class TestCaseInsensitiveMatch:
    def test_lowercase_guia(self, clf: PageClassifier) -> None:
        result = clf.classify("guía de remisión\nContenido")
        assert result.kind == "GUIA"

    def test_mixed_case_planilla(self, clf: PageClassifier) -> None:
        result = clf.classify("Planilla Resumen\nContenido")
        assert result.kind == "IGNORED"

    def test_all_caps_protocolo(self, clf: PageClassifier) -> None:
        result = clf.classify("PROTOCOLO DE RECEPCIÓN")
        assert result.kind == "DECLARED"

    def test_noise_header_case_insensitive(self, clf: PageClassifier) -> None:
        # Lowercase noise + uppercase meaningful body
        text = (
            "ptr001-torre rosales\n"
            "informe de detalle del formulario\n"
            "PLANILLA RESUMEN\n"
        )
        result = clf.classify(text)
        assert result.kind == "IGNORED"


# ---------------------------------------------------------------------------
# UNCLASSIFIED invariant (EXT-001 / EXT-002)
# ---------------------------------------------------------------------------

class TestUnclassified:
    def test_none_text_unclassified(self, clf: PageClassifier) -> None:
        result = clf.classify(None)
        assert result.kind == "UNCLASSIFIED"
        assert result.title_matched is None

    def test_empty_string_unclassified(self, clf: PageClassifier) -> None:
        result = clf.classify("")
        assert result.kind == "UNCLASSIFIED"

    def test_whitespace_only_unclassified(self, clf: PageClassifier) -> None:
        result = clf.classify("   \n  ")
        assert result.kind == "UNCLASSIFIED"

    def test_unrecognized_title_unclassified(self, clf: PageClassifier) -> None:
        result = clf.classify("ALGUN DOCUMENTO DESCONOCIDO\nContenido")
        assert result.kind == "UNCLASSIFIED"

    def test_low_confidence_on_unclassified(self, clf: PageClassifier) -> None:
        result = clf.classify("TEXTO ALEATORIO")
        assert result.confidence < LOW_CONFIDENCE_THRESHOLD

    def test_noise_only_page_unclassified_without_ocr(
        self, clf: PageClassifier
    ) -> None:
        # Only universal header/footer — no ocr_title → UNCLASSIFIED
        text = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "Created by Someone with Autodesk® Forma®\n"
            "Page 10 of 493\n"
        )
        result = clf.classify(text, ocr_title=None)
        assert result.kind == "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# Supplier name MUST NOT be used as a classifier signal (EXT-001)
# ---------------------------------------------------------------------------

class TestSupplierNameNotUsed:
    def test_aceros_arequipa_alone_unclassified(self, clf: PageClassifier) -> None:
        result = clf.classify("Aceros Arequipa S.A.\nContenido de la página")
        assert result.kind == "UNCLASSIFIED"

    def test_corporacion_aceros_alone_unclassified(
        self, clf: PageClassifier
    ) -> None:
        result = clf.classify("Corporación Aceros Arequipa S.A.\nAlgo")
        assert result.kind == "UNCLASSIFIED"

    def test_supplier_name_with_guia_classifies_correctly(
        self, clf: PageClassifier
    ) -> None:
        # The title (GUÍA DE REMISIÓN) must take precedence over supplier name
        result = clf.classify(
            "GUÍA DE REMISIÓN\nAceros Arequipa S.A.\nContenido"
        )
        assert result.kind == "GUIA"


# ---------------------------------------------------------------------------
# classify_page: page index embedding
# ---------------------------------------------------------------------------

class TestClassifyPage:
    def test_embeds_page_index(self, clf: PageClassifier) -> None:
        result = clf.classify_page(42, "GUÍA DE REMISIÓN\nContenido")
        assert result.page == 42
        assert result.kind == "GUIA"

    def test_embeds_page_index_with_noise(self, clf: PageClassifier) -> None:
        text = _with_noise(
            "Form detail\n"
            "#4252: CTR-PLC01-FR001\n"
            "Description\n"
            "232\n"
            "Notes\n"
            "some notes\n"
        )
        result = clf.classify_page(3, text)
        assert result.page == 3
        assert result.kind == "DECLARED"


# ---------------------------------------------------------------------------
# OCR title fallback
# ---------------------------------------------------------------------------

class TestOcrTitleFallback:
    def test_ocr_title_used_when_page_text_none(
        self, clf: PageClassifier
    ) -> None:
        result = clf.classify(page_text=None, ocr_title="GUÍA DE REMISIÓN")
        assert result.kind == "GUIA"

    def test_ocr_title_used_when_page_text_empty(
        self, clf: PageClassifier
    ) -> None:
        result = clf.classify(page_text="", ocr_title="GUÍA DE REMISIÓN")
        assert result.kind == "GUIA"

    def test_ocr_title_used_when_page_text_whitespace(
        self, clf: PageClassifier
    ) -> None:
        result = clf.classify(page_text="   \n  ", ocr_title="GUIA DE REMISION")
        assert result.kind == "GUIA"

    def test_ocr_title_used_when_only_noise(self, clf: PageClassifier) -> None:
        # page_text has only noise → cleaned body empty → fall back to ocr_title
        noise_only = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "Created by X with Autodesk® Forma® on May 31, 2026\n"
            "Page 5 of 493\n"
        )
        result = clf.classify(page_text=noise_only, ocr_title="GUIA DE REMISION")
        assert result.kind == "GUIA"

    def test_page_text_preferred_over_ocr_title(
        self, clf: PageClassifier
    ) -> None:
        # page_text has GUIA body; ocr_title says PLANILLA — page_text wins
        result = clf.classify(
            page_text="GUÍA DE REMISIÓN\nContenido",
            ocr_title="PLANILLA RESUMEN",
        )
        assert result.kind == "GUIA"

    def test_ocr_protocolo(self, clf: PageClassifier) -> None:
        result = clf.classify(page_text=None, ocr_title="PROTOCOLO DE RECEPCION")
        assert result.kind == "DECLARED"
        assert result.title_matched == "PROTOCOLO DE RECEPCION"

    def test_ocr_planilla_resumen(self, clf: PageClassifier) -> None:
        result = clf.classify(page_text=None, ocr_title="Planilla Resumen")
        assert result.kind == "IGNORED"

    def test_ocr_listado_barras(self, clf: PageClassifier) -> None:
        result = clf.classify(
            page_text=None, ocr_title="LISTADO DE BARRAS"
        )
        assert result.kind == "IGNORED"

    def test_ocr_unknown_unclassified(self, clf: PageClassifier) -> None:
        result = clf.classify(page_text=None, ocr_title="DOCUMENTO RANDOM")
        assert result.kind == "UNCLASSIFIED"
        assert result.confidence < LOW_CONFIDENCE_THRESHOLD

    def test_ocr_empty_unclassified(self, clf: PageClassifier) -> None:
        result = clf.classify(page_text=None, ocr_title="   ")
        assert result.kind == "UNCLASSIFIED"
