"""ReconciliationService — pure domain engine.

Invariants (spec REC-001 through REC-010):
- Groups by (registro, fecha, material_canonical, unidad) — four-field key.
- Sums quantities with Decimal arithmetic; NO cross-unit addition.
- MATCH tolerance is EXACT(0): any nonzero delta is MISMATCH (REC-010, locked).
- No I/O, no framework deps, no adapter imports (REC-008).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import NamedTuple

from reconciliation.domain.models import (
    GuiaContribution,
    GuiaDeRemision,
    MaterialLine,
    ReconciliationRow,
    Registro,
)

# Internal grouping key type
_GroupKey = NamedTuple(
    "_GroupKey",
    [
        ("registro", str),
        ("fecha", object),  # date | None
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
        """Produce one ``ReconciliationRow`` per (registro, fecha, material, unidad) group.

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
        declared_index: dict[_GroupKey, Decimal] = {}
        for registro in declared:
            for line in registro.declared_lines:
                key = _GroupKey(
                    registro=registro.numero,
                    fecha=registro.fecha_declarada,
                    material_canonical=line.description_canonical,
                    unidad=line.unidad,
                )
                declared_index[key] = declared_index.get(key, Decimal(0)) + line.cantidad

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
                    fecha=guia.fecha,
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
                )
                for g, total_qty in contrib_map.values()
            ]

            source_pages = sorted(
                {page for _g, _qty, _conf, page in guia_entries if page is not None}
            )

            confidences = [conf for _g, _qty, conf, _page in guia_entries if conf is not None]
            min_confidence = min(confidences) if confidences else None

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

            rows.append(
                ReconciliationRow(
                    registro=key.registro,
                    fecha=key.fecha,  # type: ignore[arg-type]
                    material_canonical=key.material_canonical,
                    unidad=key.unidad,
                    declared_qty=declared_qty,
                    delta=delta,
                    status=status,  # type: ignore[arg-type]
                    source_pages=source_pages,
                    min_confidence=min_confidence,
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
        fecha: object,
        line: MaterialLine,
    ) -> _GroupKey:
        """Derive a group key from a registry entry and a material line."""
        return _GroupKey(
            registro=registro,
            fecha=fecha,
            material_canonical=line.description_canonical,
            unidad=line.unidad,
        )
