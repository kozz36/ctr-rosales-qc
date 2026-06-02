"""ReconciliationService — pure domain engine.

Invariants (spec REC-001 through REC-010):
- Groups by (registro, material_canonical, unidad) — three-field key (MAT-001).
  ``fecha`` is NOT a grouping axis: a Registro N° = one reception event = one
  date, so ``registro`` disambiguates. Declared reception date and guía
  handwritten date can diverge (misfiled / vision-date noise); folding fecha
  into the key would split a true MATCH into DECLARED_MISSING + GUIA_MISSING.
  fecha-divergence detection is a deferred rev-4 feature, out of scope here.
- Sums quantities with Decimal arithmetic; NO cross-unit addition.
- MATCH tolerance is EXACT(0): any nonzero delta is MISMATCH (REC-010, locked).
- No I/O, no framework deps, no adapter imports (REC-008).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import NamedTuple

from reconciliation.domain.material_key import MatchMethod
from reconciliation.domain.models import (
    GuiaContribution,
    GuiaDeRemision,
    MaterialLine,
    ReconciliationRow,
    Registro,
)

# Worst-wins ordering for match_method aggregation (ADR-5, MAT-008).
# Higher index = worse provenance.
_MATCH_METHOD_PRIORITY: dict[str, int] = {
    "deterministic": 0,
    "codigo_sunat": 0,
    "llm_inferred": 1,
    "unresolved": 2,
}

# Internal grouping key type (MAT-001: fecha intentionally excluded — see module docstring)
_GroupKey = NamedTuple(
    "_GroupKey",
    [
        ("registro", str),
        ("material_canonical", str),
        ("unidad", str),
    ],
)


class ReconciliationService:
    """Groups, sums, and compares guía extractions against declared quantities.

    This is a pure value service — instantiate once and call ``reconcile``
    with each batch of inputs.  No state is held between calls.
    """

    def reconcile(
        self,
        declared: list[Registro],
        guias: list[GuiaDeRemision],
    ) -> list[ReconciliationRow]:
        """Produce one ``ReconciliationRow`` per (registro, material, unidad) group.

        Spec: REC-001 through REC-010, REC-C02, REC-C05, REC-C07.

        Rev-2: ``ReconciliationRow.guias`` is populated inline as a list of
        ``GuiaContribution`` objects.  ``summed_qty`` is a computed property on
        the row (derived from ``guias[*].cantidad``) — it is never written directly.

        Args:
            declared: List of declared-side Registro objects (trusted digital source).
            guias: List of guías extracted from scanned PDF pages.

        Returns:
            One row per unique group key.  Every key present in either ``declared``
            or ``guias`` generates a row; no group is silently dropped (REC-007).
            Guías with ``registro=None`` surface in ``unresolved_guias`` (REC-C05).
        """
        # Build declared index: key -> declared_qty
        # Also remember the declared reception date per group (MAT-001): fecha is no
        # longer a grouping axis, but the output row still carries it for display.
        declared_index: dict[_GroupKey, Decimal] = {}
        declared_fecha: dict[_GroupKey, object] = {}  # key -> date | None
        for registro in declared:
            for line in registro.declared_lines:
                key = _GroupKey(
                    registro=registro.numero,
                    material_canonical=line.description_canonical,
                    unidad=line.unidad,
                )
                declared_index[key] = declared_index.get(key, Decimal(0)) + line.cantidad
                declared_fecha.setdefault(key, registro.fecha_declarada)

        # Build guía index: key -> list of _GuiaEntry (contribution + meta)
        # Each entry carries the GuiaDeRemision reference for building GuiaContribution objects.
        _GuiaEntry = tuple[GuiaDeRemision, Decimal, float | None, int | None]
        guia_index: dict[_GroupKey, list[_GuiaEntry]] = defaultdict(list)

        for guia in guias:
            if guia.registro is None:
                # Guías without assigned registro skipped from row grouping;
                # they surface as unresolved_guias (REC-C05).
                continue
            effective_registro = guia.registro
            for line in guia.lines:
                key = _GroupKey(
                    registro=effective_registro,
                    material_canonical=line.description_canonical,
                    unidad=line.unidad,
                )
                guia_index[key].append((guia, line.cantidad, line.confidence, line.source_page))

        # Union of all keys
        all_keys = set(declared_index.keys()) | set(guia_index.keys())

        rows: list[ReconciliationRow] = []
        for key in all_keys:
            declared_qty = declared_index.get(key, None)
            guia_entries = guia_index.get(key, [])

            # Build GuiaContribution objects for this group.
            # Contributions are keyed by guia_id; each guia contributes once per group
            # with the summed cantidad across all its lines in this group.
            contrib_map: dict[str, tuple[GuiaDeRemision, Decimal]] = {}
            for entry_guia, cantidad, _conf, _page in guia_entries:
                existing = contrib_map.get(entry_guia.guia_id)
                if existing is None:
                    contrib_map[entry_guia.guia_id] = (entry_guia, cantidad)
                else:
                    contrib_map[entry_guia.guia_id] = (existing[0], existing[1] + cantidad)

            contributions: list[GuiaContribution] = [
                GuiaContribution(
                    guia_id=g.guia_id,
                    source_pages=g.source_pages,
                    cantidad=total_qty,
                    # contribution MUST carry the group's unit (domain invariant)
                    unidad=key.unidad,
                    confidence=g.identity_confidence,
                    identity_source=g.identity_source,
                    # Rev-3 D5 (REC-C07): propagate year_inferred provenance.
                    year_inferred=g.year_inferred,
                )
                for g, total_qty in contrib_map.values()
            ]

            source_pages = sorted(
                {page for _g, _qty, _conf, page in guia_entries if page is not None}
            )

            confidences = [conf for _g, _qty, conf, _page in guia_entries if conf is not None]
            min_confidence = min(confidences) if confidences else None

            # Propagate requires_review from contributing guías (EXT-S08, EXT-S08b, REV-004):
            # True when any contributing guía has a null fecha (vision returned no date),
            # OR any line on a contributing guía has requires_review=True.
            # Use guia_id-keyed dict for dedup (GuiaDeRemision is not hashable).
            seen_ids: dict[str, GuiaDeRemision] = {}
            for entry_guia, _qty, _conf, _page in guia_entries:
                seen_ids.setdefault(entry_guia.guia_id, entry_guia)
            contributing_guias_list = list(seen_ids.values())
            row_requires_review = any(g.fecha is None for g in contributing_guias_list) or any(
                line.requires_review
                for g in contributing_guias_list
                for line in g.lines
            )

            # R8.5 (MAT-008): worst-wins match_method aggregation (ADR-5).
            # Scope: all declared lines + all contributing guía lines.
            # Worst = highest _MATCH_METHOD_PRIORITY value.
            # Also includes match_method from the declared_index lines (via the key lookup).
            _all_methods: list[str] = []
            for g in contributing_guias_list:
                for line in g.lines:
                    _all_methods.append(line.match_method)
            # Declared lines are not directly accessible here via the index (only qty is stored).
            # They are accessed from the declared Registro objects above; we infer them from
            # the key scan below by scanning declared lines matching this group key.
            # Simple approach: scan all declared lines matching this key's canonical+unidad.
            for reg in declared:
                for dline in reg.declared_lines:
                    if (
                        dline.description_canonical == key.material_canonical
                        and dline.unidad == key.unidad
                    ):
                        _all_methods.append(dline.match_method)

            if _all_methods:
                worst = max(_all_methods, key=lambda m: _MATCH_METHOD_PRIORITY.get(m, 0))
                row_match_method: MatchMethod = worst  # type: ignore[assignment]
            else:
                row_match_method = "deterministic"

            # Additive: requires_review is True when match_method != deterministic OR
            # any other review condition already flagged.
            if row_match_method != "deterministic":
                row_requires_review = True

            if declared_qty is None:
                # Guía exists but no declared counterpart
                status: str = "DECLARED_MISSING"
                declared_qty = Decimal(0)
                delta = sum((c.cantidad for c in contributions), start=Decimal(0))
            elif not guia_entries:
                # Declared exists but no guía rows
                status = "GUIA_MISSING"
                # contributions is empty → summed_qty will be 0 (computed property)
                delta = Decimal(0) - declared_qty
            else:
                summed = sum((c.cantidad for c in contributions), start=Decimal(0))
                delta = summed - declared_qty
                # EXACT(0) tolerance — REC-010
                status = "MATCH" if delta == Decimal(0) else "MISMATCH"

            # Display fecha (MAT-001): no longer a grouping axis. Prefer the
            # declared reception date for declared-bearing groups; for guía-only
            # groups fall back to a contributing guía's handwritten fecha.
            row_fecha = declared_fecha.get(key)
            if row_fecha is None:
                row_fecha = next(
                    (g.fecha for g in contributing_guias_list if g.fecha is not None),
                    None,
                )

            rows.append(
                ReconciliationRow(
                    registro=key.registro,
                    fecha=row_fecha,  # type: ignore[arg-type]
                    material_canonical=key.material_canonical,
                    unidad=key.unidad,
                    declared_qty=declared_qty,
                    delta=delta,
                    status=status,  # type: ignore[arg-type]
                    source_pages=source_pages,
                    min_confidence=min_confidence,
                    requires_review=row_requires_review,
                    match_method=row_match_method,
                    guias=contributions,
                )
            )

        return rows

    def apply_reassignment(
        self,
        guias: list[GuiaDeRemision],
        guia_id: str,
        new_registro: str,
        new_fecha: object,  # date | None
    ) -> list[GuiaDeRemision]:
        """Return a new list of GuiaDeRemision with the target guía reassigned.

        Spec: REC-006.

        This is a pure transformation — no mutation of the input list.

        Args:
            guias: Current list of all guías.
            guia_id: Identifier of the guía to reassign.
            new_registro: New registro number for the guía.
            new_fecha: New reception date for the guía.

        Returns:
            New list of GuiaDeRemision with the target guía updated.
        """
        result: list[GuiaDeRemision] = []
        for guia in guias:
            if guia.guia_id == guia_id:
                updated = guia.model_copy(
                    update={
                        "registro": new_registro,
                        "fecha": new_fecha,
                    }
                )
                result.append(updated)
            else:
                result.append(guia)
        return result

    @staticmethod
    def _build_line_key(
        registro: str,
        line: MaterialLine,
    ) -> _GroupKey:
        """Derive a group key from a registry entry and a material line (MAT-001: no fecha)."""
        return _GroupKey(
            registro=registro,
            material_canonical=line.description_canonical,
            unidad=line.unidad,
        )
