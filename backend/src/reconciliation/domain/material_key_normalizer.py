"""MaterialKeyNormalizer — deterministic regex parser for canonical material keys (R8.2).

Pure domain module — stdlib + Pydantic only. No I/O, no LLM, no adapter imports.

Spec: MAT-003, MAT-004, MAT-005, MAT-009, ADR-1, ADR-3.

This module composes MaterialNormalizer (NFC + lowercase + whitespace collapse)
as a pre-clean step, then applies ordered regex tables to extract the four
CanonicalKey dimensions: familia, grado, diámetro, presentación.

If ALL FOUR dimensions can be extracted deterministically → returns CanonicalKey.
If ANY dimension is ambiguous (None) → returns None (caller falls to LLM/unresolved).
"""

from __future__ import annotations

import re
from typing import Final

from reconciliation.domain.material_key import CanonicalKey
from reconciliation.domain.normalizer import MaterialNormalizer

# ---------------------------------------------------------------------------
# Grade patterns (MAT-003)
# All known dual-grade variants → "A615 G60" (Aceros Arequipa rebar)
# Order matters: more-specific patterns first.
# Applied AFTER NFC + lowercase.
# ---------------------------------------------------------------------------

_GRADE_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    # "ag615/a706 g60" and "a615/a706 g60" — slash-separated dual grade
    (re.compile(r"ag?615/a706\s*g?60", re.IGNORECASE), "A615 G60"),
    # "a a615-g60" — space-separated with hyphen
    (re.compile(r"a\s+a615[-\s]g?60", re.IGNORECASE), "A615 G60"),
    # "a615 g60" — space-separated (also handles "a615 g 60" with extra space)
    (re.compile(r"a615\s+g?60", re.IGNORECASE), "A615 G60"),
    # "a615" last-resort — bare grade with no G60 qualifier
    (re.compile(r"\ba615\b", re.IGNORECASE), "A615 G60"),
]

# ---------------------------------------------------------------------------
# Diameter table (MAT-004)
# Ordered from largest to smallest compound fraction first.
# Applied AFTER grade extraction, on the pre-cleaned text.
# Each pattern accepts: the canonical fraction/mm value plus common suffix variants.
# ---------------------------------------------------------------------------

_DIAMETER_TABLE: Final[list[tuple[re.Pattern[str], str]]] = [
    # 1 3/8" — compound fraction MUST come before "1\"" and "3/8\""
    (re.compile(r'1\s*3/8\s*(?:"|pulg(?:ada)?|\'\')?', re.IGNORECASE), '1 3/8"'),
    # 1"
    (re.compile(r'\b1\s*(?:"|pulg(?:ada)?|\'\')', re.IGNORECASE), '1"'),
    # 3/4"
    (re.compile(r'3/4\s*(?:"|pulg(?:ada)?|\'\')?', re.IGNORECASE), '3/4"'),
    # 5/8"
    (re.compile(r'5/8\s*(?:"|pulg(?:ada)?|\'\')?', re.IGNORECASE), '5/8"'),
    # 1/2"
    (re.compile(r'1/2\s*(?:"|pulg(?:ada)?|\'\')?', re.IGNORECASE), '1/2"'),
    # 3/8"
    (re.compile(r'3/8\s*(?:"|pulg(?:ada)?|\'\')?', re.IGNORECASE), '3/8"'),
    # 8mm
    (re.compile(r'8\s*mm', re.IGNORECASE), "8mm"),
]

# Canonical diameter set for hallucination guard in resolver
CANONICAL_DIAMETERS: Final[frozenset[str]] = frozenset({
    '8mm', '3/8"', '1/2"', '5/8"', '3/4"', '1"', '1 3/8"'
})

# ---------------------------------------------------------------------------
# Presentación signals (MAT-005)
# Exactly one must be present → 9M or DOB.
# Both present OR neither present → None (ambiguous).
# ---------------------------------------------------------------------------

_9M_SIGNALS: Final[list[re.Pattern[str]]] = [
    re.compile(r'x\s*9\s*m\b', re.IGNORECASE),
    re.compile(r'x9m', re.IGNORECASE),
    re.compile(r'\b9\s*m\b', re.IGNORECASE),
]

_DOB_SIGNALS: Final[list[re.Pattern[str]]] = [
    re.compile(r'\bdob\b', re.IGNORECASE),
    re.compile(r'\bdimensionado\b', re.IGNORECASE),
    re.compile(r'\bapl\b', re.IGNORECASE),
    re.compile(r'acero\s+dimensionado', re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Familia patterns
# ---------------------------------------------------------------------------

_FAMILIA_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(r'\bbarra\b', re.IGNORECASE), "BARRA"),
    (re.compile(r'acero\s+dimensionado', re.IGNORECASE), "BARRA"),
]

# ---------------------------------------------------------------------------
# MaterialKeyNormalizer
# ---------------------------------------------------------------------------


class MaterialKeyNormalizer:
    """Deterministic regex parser for material descriptions.

    Composes MaterialNormalizer as the pre-clean step (NFC + lowercase +
    whitespace collapse), then applies regex tables in order to extract
    familia, grado, diámetro, and presentación.

    Returns a CanonicalKey with method="deterministic" when ALL FOUR fields
    are resolved.  Returns None when ANY field is ambiguous (caller should
    fall through to LLM inference or unresolved sentinel).

    Pure domain: no I/O, no LLM, no adapter imports.
    """

    def __init__(self) -> None:
        self._pre_clean = MaterialNormalizer()

    def parse(self, raw: str, unidad: str) -> CanonicalKey | None:
        """Parse a raw material description into a CanonicalKey.

        Args:
            raw:    Raw description string (may contain mixed case, accents,
                    supplier prefixes, punctuation noise).
            unidad: Unit of measure ("KG", "TN", "RD", "Rollo"). Passed
                    through as-is; NEVER converted.

        Returns:
            CanonicalKey with method="deterministic" if all four dimensions
            (familia, grado, diámetro, presentación) can be extracted.
            None if any dimension is ambiguous or unknown.
        """
        cleaned = self._pre_clean.canonicalize(raw)

        familia = self._extract_familia(cleaned)
        if familia is None:
            return None

        grado = self._extract_grado(cleaned)
        if grado is None:
            return None

        diametro = self._extract_diametro(cleaned)
        if diametro is None:
            return None

        presentacion = self._extract_presentacion(cleaned)
        if presentacion is None:
            return None

        return CanonicalKey(
            familia=familia,
            grado=grado,
            diametro=diametro,
            presentacion=presentacion,
            unidad=unidad,  # type: ignore[arg-type]
            method="deterministic",
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Private extraction helpers
    # ------------------------------------------------------------------

    def _extract_familia(self, cleaned: str) -> str | None:
        for pattern, canonical in _FAMILIA_PATTERNS:
            if pattern.search(cleaned):
                return canonical
        return None

    def _extract_grado(self, cleaned: str) -> str | None:
        for pattern, canonical in _GRADE_PATTERNS:
            if pattern.search(cleaned):
                return canonical
        return None

    def _extract_diametro(self, cleaned: str) -> str | None:
        for pattern, canonical in _DIAMETER_TABLE:
            if pattern.search(cleaned):
                return canonical
        return None

    def _extract_presentacion(self, cleaned: str) -> str | None:
        has_9m = any(p.search(cleaned) for p in _9M_SIGNALS)
        has_dob = any(p.search(cleaned) for p in _DOB_SIGNALS)

        if has_9m and has_dob:
            # Contradictory signals — ambiguous
            return None
        if has_9m:
            return "9M"
        if has_dob:
            return "DOB"
        # Neither signal — ambiguous
        return None
