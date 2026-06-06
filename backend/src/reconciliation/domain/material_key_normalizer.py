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
# The spec family is the Aceros Arequipa dual cert A615/A706 (≡ bare A615).
# The grade NUMBER (60/42/75) is captured SEPARATELY so valid grades stay
# distinct: G60 is the standard, but G42 and G75 are valid and MUST NOT be
# collapsed into G60.  Canonical grade = "A615 G{n}".
# Applied AFTER NFC + lowercase.
# ---------------------------------------------------------------------------

# Spec-family detector: matches the A615 dual-cert family in all real-corpus
# spellings — bare ``a615``, slash dual ``a615/a706`` / ``ag615/a706``, and the
# physical-guía concatenations WITHOUT a slash: ``a615a706``, ``a6151a706``
# (stray OCR "1" between 615 and a706), ``a615-a706``, ``a615 a706``.
# A leading optional "a " (as in "a a615-g60") is tolerated by the \b anchor.
_SPEC_FAMILY_RE: Final[re.Pattern[str]] = re.compile(
    r"\ba?g?615"          # bare 615 / ag615 / a615
    r"(?:\s*[/\-\s]?\s*"   # optional separator: slash / hyphen / space / concat
    r"\d?\s*a706)?",       # optional a706 dual cert (with optional stray OCR digit)
    re.IGNORECASE,
)

# Valid canonical grade levels (Aceros Arequipa A615/A706 dual cert).
# G60 is the standard; G42 and G75 are valid and MUST stay distinct (never
# collapsed into G60).  Any token positioned as a grade whose value is NOT in
# this set is an ILLEGIBLE/INVALID grade → parse() returns None → Tier-2.
_VALID_GRADE_LEVELS: Final[frozenset[str]] = frozenset({"60", "42", "75"})

# Grade-context token detectors.  A "grade-like token" is anchored to a grade
# CONTEXT, NOT a whole-string scan — this is the JD FIX #1 core change:
#
#   1. ``_G_PREFIXED_GRADE_RE`` — a ``g`` / ``gr`` / ``grado`` prefix followed by
#      the grade payload.  OCR glue is tolerated: there may be NO boundary
#      between the ``g`` and the digits (``g660``), and the payload may carry
#      alpha-noise (``g7s``, ``g6o``).  The payload (``[a-z0-9]+``) is captured
#      and validated against ``_VALID_GRADE_LEVELS`` by the caller.  This catches
#      g-glued, 2-digit, 4-digit, and alpha-noise invalid grades that the old
#      ``\d{3}`` guard silently let default to G60.
#
#   2. ``_POST_FAMILY_NUMERIC_GRADE_RE`` — a standalone numeric token positioned
#      AS the grade: immediately after the spec family (``a615`` / ``a706``),
#      separated only by whitespace.  This is the legacy space-separated misread
#      form (``a615a706 580 ...``) and must still bail.  Anchoring to the family
#      boundary is what prevents an INCIDENTAL number elsewhere (lot ``250``,
#      ``lote 119``) from being mistaken for a grade.
#
# Both capture group 1 = the grade payload string the caller validates.
_G_PREFIXED_GRADE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bg(?:rado|r)?[-\s]*([a-z0-9]+)",
    re.IGNORECASE,
)
# NOTE: the captured digit run must NOT be the leading whole of a diameter
# fraction (``3/4"`` → ``3``) nor a millimetre diameter (``8mm`` → ``8``); a
# trailing ``/`` or ``mm`` negative-lookahead excludes those so a bare-A615 line
# whose next token is the diameter does not spuriously read the diameter as a
# grade.  ``8mm`` is the only mm diameter and ``8`` is not a valid grade, so the
# guard matters.
_POST_FAMILY_NUMERIC_GRADE_RE: Final[re.Pattern[str]] = re.compile(
    r"a(?:615|706)\s+(\d+)(?!\s*/)(?!\s*mm)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Diameter table (MAT-004)
# Ordered from largest to smallest compound fraction first.
# Applied AFTER grade extraction, on the pre-cleaned text.
# Each pattern accepts: the canonical fraction/mm value plus common suffix variants.
# ---------------------------------------------------------------------------

_DIAMETER_TABLE: Final[list[tuple[re.Pattern[str], str]]] = [
    # 1 3/8" — compound fraction MUST come before "1\"" and "3/8\""
    # SUNAT GRE (Aceros Arequipa) writes the whole/fraction separator as a DOT
    # ("1.3/8"), while Forma uses whitespace ("1 3/8"); accept dot/hyphen/none too.
    # \b anchors the leading "1" so it never false-matches the "1" inside "a615".
    (re.compile(r'\b1\s*[.\-]?\s*3/8\s*(?:"|pulg(?:ada)?|\'\')?', re.IGNORECASE), '1 3/8"'),
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

    def parse_partial(self, raw: str) -> tuple[str, str, str] | None:
        """Extract the NON-grade attributes ``(familia, diámetro, presentación)``.

        The Tier-2 grade-tolerant reconciliation primitive: when ``parse()``
        fails ONLY because the grade token is illegible (OCR misread), the
        remaining three attributes still identify the declared item. Returns
        the triple when all three are extractable, else ``None`` (a missing
        familia/diámetro/presentación means the line cannot be matched on grade
        alone — never guess).

        Pure: no grade is inferred here; grade adoption happens in the
        reconciliation layer against a UNIQUE same-registro declared item.
        """
        cleaned = self._pre_clean.canonicalize(raw)
        familia = self._extract_familia(cleaned)
        diametro = self._extract_diametro(cleaned)
        presentacion = self._extract_presentacion(cleaned)
        if familia is None or diametro is None or presentacion is None:
            return None
        return (familia, diametro, presentacion)

    # ------------------------------------------------------------------
    # Private extraction helpers
    # ------------------------------------------------------------------

    def _extract_familia(self, cleaned: str) -> str | None:
        for pattern, canonical in _FAMILIA_PATTERNS:
            if pattern.search(cleaned):
                return canonical
        return None

    def _extract_grado(self, cleaned: str) -> str | None:
        """Extract the canonical grade ``A615 G{n}``.

        Requires the A615 spec family.  Grade detection is ANCHORED to a grade
        CONTEXT (a ``g``/``gr``/``grado`` prefix, or a numeric token positioned
        immediately after the spec family) — NOT a whole-string ``\\d{3}`` scan.
        This is the JD FIX #1 core: an incidental lot/qty number elsewhere in
        the string (``lote 119``, ``... dob 250``) is NOT a grade context and
        must NOT trigger a bail, while a g-glued / 2-digit / 4-digit / alpha-
        noise invalid grade (``g660``, ``g50``, ``g6042``, ``g7s``) IS a grade
        context and MUST bail to None.

        Resolution rules (after the spec-family gate):
          1. Collect every grade-context token and validate each against
             ``_VALID_GRADE_LEVELS`` (60/42/75).
          2. If ANY grade-context token is INVALID → None (Tier-2 / requires_review).
             Invalid grades must never silently default to G60.
          3. If >1 DISTINCT valid grade is present (e.g. ``g 60`` AND ``g 75``)
             → None (hand to Tier-2/LLM, never arbitrarily pick the first).
          4. Exactly one distinct valid grade → ``A615 G{n}`` (kept distinct).
          5. No grade-context token at all (clean bare A615, incidental numbers
             allowed) → standard G60 default.
        """
        if not _SPEC_FAMILY_RE.search(cleaned):
            return None

        valid_levels: set[str] = set()
        for token_re in (_G_PREFIXED_GRADE_RE, _POST_FAMILY_NUMERIC_GRADE_RE):
            for match in token_re.finditer(cleaned):
                payload = match.group(1).lower()
                if payload in _VALID_GRADE_LEVELS:
                    valid_levels.add(payload)
                else:
                    # Grade context present but value is not a valid grade →
                    # illegible/invalid grade.  Never default to G60.
                    return None

        if len(valid_levels) > 1:
            # Contradictory legible grades → ambiguous, hand to Tier-2/LLM.
            return None
        if len(valid_levels) == 1:
            return f"A615 G{next(iter(valid_levels))}"
        # No grade-context token at all → clean bare A615 → standard G60.
        return "A615 G60"

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
