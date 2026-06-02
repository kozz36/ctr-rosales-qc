"""PageClassifier — classifies PDF pages by document title.

Classification rules (EXT-001):
- Uses ONLY the document title text.
- MUST NOT use supplier name as a signal.
- Any page that does not match a known title is UNCLASSIFIED (never silently dropped).

Locked constant: LOW_CONFIDENCE_THRESHOLD = 0.85 (EXT-002, locked-defaults #2).

Implementation notes (real-PDF hardening):
- Every page in the target PDF begins with a universal header:
    "PTR001-TORRE ROSALES" / "Informe de detalle del formulario"
  followed by an optional "Created by … Autodesk … Forma …" footer and
  "Page N of M" line.  These lines are stripped before any matching.
- The true document type appears *later* in the text, not on line 1.
  Therefore the classifier scans the WHOLE cleaned text for known markers.
- Scanned pages have only the 4-line header/footer overlay and yield an
  empty cleaned body; the classifier then consults ocr_title.

Rev-3 hybrid OR-gate (EXT-019 / D1):
- ``classify`` and ``classify_page`` accept two new optional booleans computed
  by the pipeline from the decode_identities pre-pass:
    ``qr_is_guia``: True when a compact SUNAT GRE QR passed the EXT-012 gate
                    (Condition A — authoritative).
    ``image_dominant``: True when the page's raster image coverage >= threshold
                        (used in Condition B heuristic).
- Evaluation order (declared-title-first, EXT-S25):
    1. Declared/protocolo title match (text body) — wins over any QR signal.
    2. Condition A: qr_is_guia  → GUIA (QR_IDENTITY).
    3. Condition C: "GUIA DE REMISION" in text/ocr_title (existing EXT-001).
    4. Condition B: header-only (<= _FORMA_HEADER_MAX_CHARS cleaned chars) AND
                    image_dominant → GUIA (FORMA_HEADER_HEURISTIC).
    5. No match → UNCLASSIFIED.
- The classifier remains PURE: it receives plain booleans, not ports/adapters.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from reconciliation.domain.models import PageClassification

_KindLiteral = Literal["GUIA", "DECLARED", "IGNORED", "UNCLASSIFIED"]

LOW_CONFIDENCE_THRESHOLD: float = 0.85

_HIGH_CONFIDENCE: float = 0.99
_LOW_CONFIDENCE: float = 0.30

# Rev-3 Condition B (EXT-019): maximum cleaned-body char count for the
# Forma-header-only heuristic.  Declared/protocolo pages always exceed this.
_FORMA_HEADER_MAX_CHARS: int = 200

# Rev-3: minimum image coverage ratio to trigger Condition B ("image_dominant").
IMAGE_DOMINANT_THRESHOLD: float = 0.5

# ---------------------------------------------------------------------------
# Noise patterns — lines that are present on EVERY page and carry no signal.
# Matching is done case-insensitively on the NFC-normalised uppercase line.
# ---------------------------------------------------------------------------

_NOISE_EXACT: frozenset[str] = frozenset(
    {
        "PTR001-TORRE ROSALES",
        "INFORME DE DETALLE DEL FORMULARIO",
    }
)

# Prefix/substring patterns for noise lines (applied after uppercase + strip)
_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^CREATED\s+BY\s+.+AUTODESK"),     # footer authorship
    re.compile(r"^PAGE\s+\d+\s+OF\s+\d+$"),         # page-N-of-M footer
)


def _normalize(text: str) -> str:
    """NFC-normalise, uppercase, and collapse internal whitespace."""
    return " ".join(unicodedata.normalize("NFC", text).upper().split())


def _is_noise(line: str) -> bool:
    """Return True if the line is universal header/footer noise."""
    n = _normalize(line)
    if n in _NOISE_EXACT:
        return True
    return any(p.search(n) for p in _NOISE_PATTERNS)


def _clean_lines(page_text: str) -> list[str]:
    """Strip noise lines and return non-empty content lines (uppercased, NFC)."""
    result: list[str] = []
    for raw in page_text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if _is_noise(stripped):
            continue
        result.append(_normalize(stripped))
    return result


# ---------------------------------------------------------------------------
# Classification predicates — checked in priority order inside classify().
# ---------------------------------------------------------------------------

_RE_RECORD_MARKER = re.compile(r"#\d+:")   # e.g. "#4252:"


def _match_protocolo(lines: list[str]) -> bool:
    return any(
        "PROTOCOLO DE RECEPCI" in l    # covers both "RECEPCION" and "RECEPCIÓN"
        for l in lines
    )


def _match_guia(lines: list[str]) -> bool:
    return any(
        "GUIA DE REMISI" in l or "GUÍA DE REMISI" in l
        for l in lines
    )


def _match_detail_declared(lines: list[str]) -> bool:
    """Classify as DECLARED (Autodesk Form Detail page).

    Signal: "FORM DETAIL" is present AND either a #<digits>: record marker
    exists OR both "DESCRIPTION" and "NOTES" fields are present.
    These are the inner-form metadata lines; they appear only on proper
    detail pages, not on the cover.
    """
    has_form_detail = any(l == "FORM DETAIL" for l in lines)
    if not has_form_detail:
        return False
    has_record = any(_RE_RECORD_MARKER.search(l) for l in lines)
    has_desc_and_notes = (
        any(l == "DESCRIPTION" for l in lines)
        and any(l == "NOTES" for l in lines)
    )
    return has_record or has_desc_and_notes


def _match_ignored_cover(lines: list[str]) -> bool:
    """Cover / summary page: 'Total items', 'Sorted by', 'Filtered by' metadata."""
    return any(
        l in ("TOTAL ITEMS", "SORTED BY", "FILTERED BY")
        for l in lines
    )


def _match_ignored_contents(lines: list[str]) -> bool:
    """Table of contents page: contains "CONTENTS" but no Form Detail header."""
    return any(l == "CONTENTS" for l in lines)


def _match_planilla_resumen(lines: list[str]) -> bool:
    return any("PLANILLA RESUMEN" in l for l in lines)


def _match_listado_barras(lines: list[str]) -> bool:
    return any("LISTADO DE BARRAS" in l for l in lines)


def _match_caratula(lines: list[str]) -> bool:
    return any(l in ("CARATULA", "CARÁTULA") for l in lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _normalize_title(text: str) -> str:
    """Backwards-compatible alias used by classify() for ocr_title normalisation."""
    return _normalize(text)


class PageClassifier:
    """Classifies a PDF page by its document title text.

    Spec: EXT-001, EXT-002.  Rev-3: EXT-019 hybrid OR-gate.

    The classifier scans the *whole* cleaned page body (after stripping
    universal header/footer noise) for known markers, rather than relying
    on the first line.  This handles real-world PDFs where the document
    type identifier is buried below a multi-line header common to all pages.

    Priority order (rev-3 hybrid, EXT-019):
        1. PROTOCOLO DE RECEPCION  → DECLARED  (digital text; highest priority)
        2. Condition A: qr_is_guia  → GUIA (QR_IDENTITY)
        3. GUIA DE REMISION in text → GUIA (digital/ocr title — Condition C)
        4. Cover metadata           → IGNORED
        5. Contents page            → IGNORED
        6. Planilla Resumen         → IGNORED
        7. Listado de Barras        → IGNORED
        8. Carátula                 → IGNORED
        9. Form Detail page         → DECLARED
        10. Condition B: header-only (<= 200 chars) AND image_dominant → GUIA (heuristic)
        11. No match                → UNCLASSIFIED
    """

    def classify(
        self,
        page_text: str | None,
        ocr_title: str | None = None,
        *,
        qr_is_guia: bool = False,
        image_dominant: bool = False,
    ) -> PageClassification:
        """Classify a page using ``page_text`` (preferred) or ``ocr_title`` as fallback.

        Rev-3: accepts two additional pure-boolean signals from the pipeline's
        decode_identities pre-pass (EXT-019 / D1).

        Args:
            page_text: Full digital text extracted from the page (may be None/empty
                for scanned pages).
            ocr_title: Optional OCR-extracted title string.  Used when page_text
                is None or contains only universal header/footer noise.
            qr_is_guia: True when the page's QR decode passed the EXT-012 confidence
                gate (Condition A).  The classifier receives only the boolean verdict,
                never the adapter or the raw identity object.
            image_dominant: True when the page's raster image coverage exceeds the
                threshold (used in Condition B heuristic).

        Returns:
            PageClassification with the assigned kind and confidence score.
        """
        # Build the cleaned body from page_text (noise stripped)
        body_lines: list[str] = []
        if page_text and page_text.strip():
            body_lines = _clean_lines(page_text)

        # If cleaning reduced the page to nothing, fall back to hybrid-aware OCR path
        if not body_lines:
            return self._classify_from_hybrid(ocr_title, qr_is_guia, image_dominant)

        # --- Declared-title-first guard (EXT-S25) ---
        # A page with substantial declared text MUST win over any QR/heuristic signal.

        # 1. Protocolo (must precede GUIA check — protocolo pages also contain
        #    "GUIA DE REMISION" as a form field label)
        if _match_protocolo(body_lines):
            return PageClassification(
                page=0,
                kind="DECLARED",
                title_matched="PROTOCOLO DE RECEPCION",
                confidence=_HIGH_CONFIDENCE,
            )

        # 2. Condition A: QR identity gate (authoritative if no declared text won above)
        if qr_is_guia:
            return PageClassification(
                page=0,
                kind="GUIA",
                title_matched="QR_IDENTITY",
                confidence=_HIGH_CONFIDENCE,
            )

        # 3. Condition C: Guía de Remisión in digital text (original EXT-001 path)
        if _match_guia(body_lines):
            return PageClassification(
                page=0,
                kind="GUIA",
                title_matched="GUIA DE REMISION",
                confidence=_HIGH_CONFIDENCE,
            )

        # 4. Cover / summary page
        if _match_ignored_cover(body_lines):
            return PageClassification(
                page=0,
                kind="IGNORED",
                title_matched="COVER",
                confidence=_HIGH_CONFIDENCE,
            )

        # 5. Contents / index page
        if _match_ignored_contents(body_lines):
            return PageClassification(
                page=0,
                kind="IGNORED",
                title_matched="CONTENTS",
                confidence=_HIGH_CONFIDENCE,
            )

        # 6. Planilla Resumen
        if _match_planilla_resumen(body_lines):
            return PageClassification(
                page=0,
                kind="IGNORED",
                title_matched="PLANILLA RESUMEN",
                confidence=_HIGH_CONFIDENCE,
            )

        # 7. Listado de Barras
        if _match_listado_barras(body_lines):
            return PageClassification(
                page=0,
                kind="IGNORED",
                title_matched="LISTADO DE BARRAS",
                confidence=_HIGH_CONFIDENCE,
            )

        # 8. Carátula (legacy rule)
        if _match_caratula(body_lines):
            return PageClassification(
                page=0,
                kind="IGNORED",
                title_matched="CARATULA",
                confidence=_HIGH_CONFIDENCE,
            )

        # 9. Autodesk Form Detail page (detail record)
        if _match_detail_declared(body_lines):
            return PageClassification(
                page=0,
                kind="DECLARED",
                title_matched="FORM DETAIL",
                confidence=_HIGH_CONFIDENCE,
            )

        # 10. Condition B: Forma-header-only heuristic (EXT-019, D1).
        # Guard: only fires when the cleaned body is <= _FORMA_HEADER_MAX_CHARS chars
        # AND the page is image-dominant.  Declared/protocolo pages (step 1/9 above)
        # always exceed 200 chars so they can NEVER reach this branch.
        body_char_count = sum(len(l) for l in body_lines)
        if body_char_count <= _FORMA_HEADER_MAX_CHARS and image_dominant:
            return PageClassification(
                page=0,
                kind="GUIA",
                title_matched="FORMA_HEADER_HEURISTIC",
                confidence=_HIGH_CONFIDENCE,
            )

        # 11. Fallback: nothing matched
        return PageClassification(
            page=0,
            kind="UNCLASSIFIED",
            title_matched=None,
            confidence=_LOW_CONFIDENCE,
        )

    def classify_page(
        self,
        page_index: int,
        page_text: str | None,
        ocr_title: str | None = None,
        *,
        qr_is_guia: bool = False,
        image_dominant: bool = False,
    ) -> PageClassification:
        """Classify and embed the correct page index in the result.

        Convenience wrapper used by the pipeline where the page index is known.
        Rev-3: forwards qr_is_guia and image_dominant to classify() (EXT-019).
        """
        result = self.classify(
            page_text,
            ocr_title,
            qr_is_guia=qr_is_guia,
            image_dominant=image_dominant,
        )
        return result.model_copy(update={"page": page_index})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_from_ocr(ocr_title: str | None) -> PageClassification:
        """Classify using only ocr_title when page_text body is empty (scanned page).

        Legacy path kept for callers that do not provide the hybrid booleans.
        Use ``_classify_from_hybrid`` for the rev-3 path.
        """
        if not ocr_title or not ocr_title.strip():
            return PageClassification(
                page=0,
                kind="UNCLASSIFIED",
                title_matched=None,
                confidence=_LOW_CONFIDENCE,
            )
        n = _normalize(ocr_title)
        if "PROTOCOLO DE RECEPCI" in n:
            return PageClassification(
                page=0,
                kind="DECLARED",
                title_matched="PROTOCOLO DE RECEPCION",
                confidence=_HIGH_CONFIDENCE,
            )
        if "GUIA DE REMISI" in n or "GUÍA DE REMISI" in n:
            return PageClassification(
                page=0,
                kind="GUIA",
                title_matched="GUIA DE REMISION",
                confidence=_HIGH_CONFIDENCE,
            )
        if "PLANILLA RESUMEN" in n:
            return PageClassification(
                page=0,
                kind="IGNORED",
                title_matched="PLANILLA RESUMEN",
                confidence=_HIGH_CONFIDENCE,
            )
        if "LISTADO DE BARRAS" in n:
            return PageClassification(
                page=0,
                kind="IGNORED",
                title_matched="LISTADO DE BARRAS",
                confidence=_HIGH_CONFIDENCE,
            )
        return PageClassification(
            page=0,
            kind="UNCLASSIFIED",
            title_matched=None,
            confidence=_LOW_CONFIDENCE,
        )

    @staticmethod
    def _classify_from_hybrid(
        ocr_title: str | None,
        qr_is_guia: bool,
        image_dominant: bool,
    ) -> PageClassification:
        """Rev-3 path: classify a scanned page (empty body) using hybrid signals.

        When the cleaned body is empty (page is scanned / header-only), apply:
          1. Condition A: qr_is_guia → GUIA (QR_IDENTITY), highest weight.
          2. ocr_title title match (Condition C legacy path).
          3. Condition B: image_dominant (since body is empty, char count is 0
             which is always <= _FORMA_HEADER_MAX_CHARS) → GUIA heuristic.
          4. UNCLASSIFIED.
        """
        # Condition A: authoritative QR signal
        if qr_is_guia:
            return PageClassification(
                page=0,
                kind="GUIA",
                title_matched="QR_IDENTITY",
                confidence=_HIGH_CONFIDENCE,
            )

        # Condition C via ocr_title
        if ocr_title and ocr_title.strip():
            n = _normalize(ocr_title)
            if "PROTOCOLO DE RECEPCI" in n:
                return PageClassification(
                    page=0,
                    kind="DECLARED",
                    title_matched="PROTOCOLO DE RECEPCION",
                    confidence=_HIGH_CONFIDENCE,
                )
            if "GUIA DE REMISI" in n or "GUÍA DE REMISI" in n:
                return PageClassification(
                    page=0,
                    kind="GUIA",
                    title_matched="GUIA DE REMISION",
                    confidence=_HIGH_CONFIDENCE,
                )
            if "PLANILLA RESUMEN" in n:
                return PageClassification(
                    page=0,
                    kind="IGNORED",
                    title_matched="PLANILLA RESUMEN",
                    confidence=_HIGH_CONFIDENCE,
                )
            if "LISTADO DE BARRAS" in n:
                return PageClassification(
                    page=0,
                    kind="IGNORED",
                    title_matched="LISTADO DE BARRAS",
                    confidence=_HIGH_CONFIDENCE,
                )

        # Condition B: image-dominant scanned page with header-only text
        # (body is empty so char count = 0 <= _FORMA_HEADER_MAX_CHARS always holds here)
        if image_dominant:
            return PageClassification(
                page=0,
                kind="GUIA",
                title_matched="FORMA_HEADER_HEURISTIC",
                confidence=_HIGH_CONFIDENCE,
            )

        return PageClassification(
            page=0,
            kind="UNCLASSIFIED",
            title_matched=None,
            confidence=_LOW_CONFIDENCE,
        )
