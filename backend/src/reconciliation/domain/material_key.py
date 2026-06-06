"""CanonicalKey Value Object and MatchMethod literal (R8.1, MAT-002).

Pure domain module — stdlib + Pydantic only. No I/O, no adapter, no SDK.

The canonical key identifies a physical rebar product as a tuple:
    (familia, grado, diámetro, presentación, unidad)

group_token is the string serialized into MaterialLine.description_canonical
so the existing ReconciliationService grouping engine (keyed on str) works
unchanged. unidad is EXCLUDED from group_token because _GroupKey already
carries unidad as a separate axis (ADR-1).
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, computed_field

# ---------------------------------------------------------------------------
# MatchMethod
# ---------------------------------------------------------------------------

MatchMethod = Literal[
    "deterministic", "grade_tolerant", "llm_inferred", "codigo_sunat", "unresolved", "operator"
]
"""How the canonical key was derived.

- ``deterministic``:  pure regex rules, no LLM involved.
- ``grade_tolerant``: deterministic parse failed ONLY on an illegible grade token;
                      the line was merged into the UNIQUE same-registro declared item
                      with matching (familia, diámetro, presentación). Requires review.
- ``llm_inferred``:   Ollama inference was needed; row requires human review.
- ``codigo_sunat``:   reserved — SUNAT producto code authoritative join (no production path yet).
- ``unresolved``:     both deterministic and LLM failed; row requires human review.
- ``operator``:       the engineer manually reassigned the line's canonical key via Corregir
                      manual (F4 / REV-R25); always requires_review=True.
"""

# Methods that always require human review
_REQUIRES_REVIEW_METHODS: frozenset[str] = frozenset(
    {"grade_tolerant", "llm_inferred", "unresolved"}
)


# ---------------------------------------------------------------------------
# CanonicalKey VO
# ---------------------------------------------------------------------------


class CanonicalKey(BaseModel):
    """Immutable canonical material key: (familia, grado, diámetro, presentación, unidad).

    Equality is based on the SEMANTIC tuple (familia, grado, diametro, presentacion, unidad).
    The ``raw`` and ``method`` fields are provenance/audit metadata and do NOT participate
    in equality — two keys with the same semantic tuple but different source texts are equal.
    This is the core MATCH behaviour: declared↔guía descriptions normalize to the same key.

    ADR-1: group_token is the string written to MaterialLine.description_canonical.
    unidad is EXCLUDED from group_token because _GroupKey in reconciliation.py
    carries unidad as a separate axis; including it here would double-count.

    MAT-005: presentación is significant — 9M (straight bar) ≠ DOB (cut/bent).
             Two keys differing in presentación are NOT equal.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    familia: str
    grado: str | None = None
    diametro: str | None = None
    presentacion: str | None = None
    unidad: Literal["KG", "TN", "RD", "Rollo"]
    method: MatchMethod = "deterministic"
    raw: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def requires_review(self) -> bool:
        """True when method requires human review (llm_inferred or unresolved)."""
        return self.method in _REQUIRES_REVIEW_METHODS

    @computed_field  # type: ignore[prop-decorator]
    @property
    def group_token(self) -> str:
        """String key written into MaterialLine.description_canonical.

        unidad MUST be excluded — _GroupKey in reconciliation.py carries it
        as a separate axis.  Including it here would break grouping invariants.

        For unresolved keys: prefixed with "UNRESOLVED::" followed by the raw
        description (lowercased + stripped) for auditability.

        For resolved keys: "{familia} {grado|?} {diametro|?} {presentacion|?}".
        """
        if self.method == "unresolved":
            return f"UNRESOLVED::{self.raw.strip().lower()}"
        return " ".join([
            self.familia,
            self.grado or "?",
            self.diametro or "?",
            self.presentacion or "?",
        ])

    def _semantic_tuple(self) -> tuple:
        """Semantic identity tuple — used for equality and hashing."""
        return (self.familia, self.grado, self.diametro, self.presentacion, self.unidad)

    def __eq__(self, other: object) -> bool:
        """Equality based on semantic tuple only (raw and method excluded)."""
        if not isinstance(other, CanonicalKey):
            return NotImplemented
        return self._semantic_tuple() == other._semantic_tuple()

    def __hash__(self) -> int:
        """Hash based on semantic tuple only."""
        return hash(self._semantic_tuple())

    @classmethod
    def unresolved(cls, raw: str, unidad: Literal["KG", "TN", "RD", "Rollo"]) -> "CanonicalKey":
        """Factory for an UNRESOLVED sentinel key.

        Used when both deterministic regex and LLM inference fail to produce
        a valid canonical key.  Always produces requires_review=True.

        Args:
            raw:    The original raw description string (preserved for auditing).
            unidad: The unit of measure for this line.
        """
        return cls(
            familia="UNRESOLVED",
            grado=None,
            diametro=None,
            presentacion=None,
            unidad=unidad,
            method="unresolved",
            raw=raw,
        )
