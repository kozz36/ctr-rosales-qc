"""ReviewService — applies value edits and guía reassignments post-pipeline.

Responsibilities:
  - apply_edit:          update a single field value on a ReconciliationRow or
                         on a MaterialLine within a GuiaDeRemision.
  - apply_reassignment:  delegate guía-level reassignment to ReconciliationService,
                         then recompute affected rows.
  - get_audit_trail:     return the ordered list of edit events.
  - restore_from_sidecar: replay persisted edits on restart (resumability,
                          locked-defaults #4).

Design:
  - ReviewService holds no long-lived state — edits are always applied from the
    sidecar on construction (or from an empty list for a fresh session).
  - After each mutation the sidecar is atomically rewritten via RunContext.
  - ReconciliationService.apply_reassignment (pure domain) handles guía list
    transformation; ReviewService only coordinates the call and re-reconciles.
  - apply_edit is intentionally conservative: only date/string/Decimal fields
    on known paths.  Unrecognised field paths raise ValueError.
"""

from __future__ import annotations

import copy
import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from reconciliation.domain.errors import ReconciliationError
from reconciliation.domain.models import (
    ErroredGuia,
    GuiaDeRemision,
    ReconciliationRow,
    Registro,
)
from reconciliation.domain.reconciliation import ReconciliationService
from reconciliation.domain.section_id_guard import is_section_id
from reconciliation.application.run_context import RunContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Edit event schema
# ---------------------------------------------------------------------------


class EditEvent:
    """Immutable record of a single review edit.

    Attributes:
        timestamp:  ISO-8601 UTC timestamp (string for JSON serialisability).
        kind:       ``"field_edit"`` or ``"reassignment"``.
        target:     Identifies the target object (e.g. ``{"guia_id": "..."}``).
        field:      Field name (for field_edit), or None.
        old_value:  Previous value (serialised as string).
        new_value:  New value (serialised as string).
    """

    def __init__(
        self,
        kind: str,
        target: dict[str, Any],
        field: str | None,
        old_value: Any,
        new_value: Any,
    ) -> None:
        self.timestamp: str = datetime.now(tz=timezone.utc).isoformat()
        self.kind = kind
        self.target = target
        self.field = field
        # Preserve dict/list values as-is so they survive JSON round-trips.
        # Scalar values are coerced to string for uniform serialisation.
        self.old_value: Any = old_value if isinstance(old_value, (dict, list)) else (
            str(old_value) if old_value is not None else None
        )
        self.new_value: Any = new_value if isinstance(new_value, (dict, list)) else (
            str(new_value) if new_value is not None else None
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for sidecar persistence."""
        return {
            "timestamp": self.timestamp,
            "kind": self.kind,
            "target": self.target,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


# ---------------------------------------------------------------------------
# ReviewService
# ---------------------------------------------------------------------------


class ReviewService:
    """Applies and persists review edits over a completed pipeline result.

    The service operates on in-memory copies of the domain objects that were
    produced by the pipeline.  On each mutation it rewrites the review sidecar
    atomically so that a restart can replay all edits without re-running
    OCR/vision.

    Args:
        declared:   List of Registro objects from the pipeline.
        guias:      List of GuiaDeRemision objects from the pipeline.
        rows:       Initial ReconciliationRow list from the pipeline.
        ctx:        RunContext that owns the review sidecar path.
    """

    def __init__(
        self,
        declared: list[Registro],
        guias: list[GuiaDeRemision],
        rows: list[ReconciliationRow],
        ctx: RunContext,
        errored_guias: list[ErroredGuia] | None = None,
    ) -> None:
        self._declared: list[Registro] = list(declared)
        self._guias: list[GuiaDeRemision] = list(guias)
        self._rows: list[ReconciliationRow] = list(rows)
        self._ctx = ctx
        self._reconciler = ReconciliationService()
        self._audit_trail: list[EditEvent] = []
        self._errored_guias: list[ErroredGuia] = list(errored_guias) if errored_guias else []

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def rows(self) -> list[ReconciliationRow]:
        """Current reconciliation rows (after all applied edits)."""
        return list(self._rows)

    @property
    def guias(self) -> list[GuiaDeRemision]:
        """Current guías (after all applied reassignments)."""
        return list(self._guias)

    @property
    def errored_guias(self) -> list[ErroredGuia]:
        """Guías that resolved to 0 material lines (REV-E03).

        Read-only constructor state — never modified by edit/reassign events.
        """
        return list(self._errored_guias)

    def get_audit_trail(self) -> list[dict[str, Any]]:
        """Return the ordered list of edit events as serialisable dicts."""
        return [e.to_dict() for e in self._audit_trail]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def apply_edit(
        self,
        guia_id: str,
        field: str,
        new_value: Any,
    ) -> list[ReconciliationRow]:
        """Update a single field on a GuiaDeRemision and recompute affected rows.

        Supported fields:
            ``fecha``       — accepts ``date``, ISO-8601 string, or None.
            ``registro``    — accepts str or None.

        Prohibited fields (raise ValueError / 422 at API layer):
            ``summed_qty``  — computed property; use apply_guia_line_edit instead (REC-C04).

        Args:
            guia_id:    Identifier of the target GuiaDeRemision.
            field:      Field name to update (``"fecha"`` or ``"registro"``).
            new_value:  New field value.

        Returns:
            Updated list of reconciliation rows (all rows recomputed).

        Raises:
            ValueError: If the guia_id is not found, field is unsupported, or
                        field is a prohibited write target (summed_qty).
        """
        # Explicitly prohibit direct writes to computed/structural fields (REC-C04)
        if field == "summed_qty":
            raise ValueError(
                "Field 'summed_qty' is a computed property and cannot be edited directly. "
                "Use PATCH /guias/{guia_id}/lines to update a guía line quantity instead."
            )

        target_idx = self._find_guia_index(guia_id)
        guia = self._guias[target_idx]

        old_value: Any
        updated_guia: GuiaDeRemision

        if field == "fecha":
            old_value = guia.fecha
            parsed_date = _parse_date(new_value)
            updated_guia = guia.model_copy(update={"fecha": parsed_date})
        elif field == "registro":
            old_value = guia.registro
            if new_value is not None and not isinstance(new_value, str):
                raise ValueError(f"'registro' must be a string or None, got {type(new_value)}")
            if is_section_id(new_value):
                raise ValueError(
                    f"{new_value!r} is a Contents/section ID, not a valid Registro N°. "
                    "Three-identifier invariant: Contents-ID != Registro N° != QR serie-numero."
                )
            updated_guia = guia.model_copy(update={"registro": new_value})
        else:
            raise ValueError(
                f"Unsupported field '{field}' for apply_edit. "
                "Supported: 'fecha', 'registro'."
            )

        event = EditEvent(
            kind="field_edit",
            target={"guia_id": guia_id},
            field=field,
            old_value=old_value,
            new_value=new_value,
        )

        # Mutate in-memory state
        new_guias = list(self._guias)
        new_guias[target_idx] = updated_guia
        self._guias = new_guias

        # Recompute all rows after the edit (carry the SUNAT delivery floor so the
        # crossed-bounds protection is not lost on the review path).
        self._rows = self._reconciler.reconcile(
            self._declared, self._guias, delivery_dates=self._delivery_dates()
        )

        # Append audit event and persist
        self._audit_trail.append(event)
        self._persist()

        return list(self._rows)

    def apply_guia_line_edit(
        self,
        guia_id: str,
        line_index: int | None,
        material_canonical: str | None,
        new_cantidad: Decimal,
        assign_material_canonical: str | None = None,
    ) -> list[ReconciliationRow]:
        """Update a specific line's ``cantidad`` on a GuiaDeRemision and recompute rows.

        Spec: REC-C04 / REV-C02 / S1.7.

        Identifies the target line by ``line_index`` (0-based within guia.lines) or
        by ``material_canonical`` when ``line_index`` is None (matches first line with
        that canonical description).  Updates the line's ``cantidad`` in-place on an
        immutable copy, then re-runs reconcile to recompute MATCH/MISMATCH statuses.

        F4 / REV-R25 (D9): when ``assign_material_canonical`` is provided, the line's
        ``description_canonical`` is reassigned to the operator-chosen declared canonical
        key, ``match_method`` is set to ``"operator"``, and ``requires_review`` is set to
        ``True``.  An immutable ``model_copy`` is used; re-reconcile follows as usual; a
        ``"manual_correction"`` audit event is emitted.  The backward-compatible path
        (``assign_material_canonical=None``) is unchanged (cantidad-only edit).

        KNOWN LIMITATION (B5): when matching by ``material_canonical`` the edit targets
        the FIRST line whose ``description_canonical`` equals the canonical key. This is
        exact for single-line guías (the common case), but a guía with MULTIPLE lines
        sharing the same canonical key is semantically lossy — the drill-down
        ``GuiaContribution`` shown to the engineer is the SUM of all such lines, yet only
        the first one is updated. A future fix would carry a stable per-line identity
        (e.g. ``source_page`` + line ordinal) so the exact contributing line can be
        addressed. Prefer passing an explicit ``line_index`` when disambiguation matters.

        Args:
            guia_id:                    Identifier of the target GuiaDeRemision.
            line_index:                 0-based index of the line to update, or None to
                                        match by material_canonical.
            material_canonical:         Canonical material description for lookup when
                                        line_index is None.
            new_cantidad:               New quantity value (must be >= 0).
            assign_material_canonical:  Operator-chosen declared canonical key to reassign
                                        this line to (F4 / REV-R25). None → cantidad-only.

        Returns:
            Updated list of reconciliation rows (all rows recomputed).

        Raises:
            ValueError: If guia_id not found, line not found, or new_cantidad < 0.
        """
        if new_cantidad < Decimal(0):
            raise ValueError(
                f"new_cantidad must be >= 0; got {new_cantidad}"
            )

        target_idx = self._find_guia_index(guia_id)
        guia = self._guias[target_idx]

        # Locate the target line
        lines = list(guia.lines)
        resolved_index: int

        if line_index is not None:
            if line_index < 0 or line_index >= len(lines):
                raise ValueError(
                    f"line_index {line_index} out of range for guia_id={guia_id!r} "
                    f"(has {len(lines)} lines)"
                )
            resolved_index = line_index
        elif material_canonical is not None:
            for i, line in enumerate(lines):
                if line.description_canonical == material_canonical:
                    resolved_index = i
                    break
            else:
                raise ValueError(
                    f"No line with description_canonical={material_canonical!r} "
                    f"found in guia_id={guia_id!r}"
                )
        else:
            raise ValueError("Either line_index or material_canonical must be provided.")

        old_line = lines[resolved_index]
        old_cantidad = old_line.cantidad

        if assign_material_canonical is not None:
            # F4 Corregir manual (REV-R25 / D9): operator-assigned canonical reassignment.
            # Immutable model_copy — never mutate the original line object.
            new_line = old_line.model_copy(update={
                "cantidad": new_cantidad,
                "description_canonical": assign_material_canonical,
                "match_method": "operator",
                "requires_review": True,
            })
            event = EditEvent(
                kind="manual_correction",
                target={
                    "guia_id": guia_id,
                    "line_index": line_index,
                    "material_canonical": material_canonical,
                },
                field="description_canonical",
                old_value=old_line.description_canonical,
                new_value=assign_material_canonical,
            )
        else:
            # Original cantidad-only path (backward-compatible).
            new_line = old_line.model_copy(update={"cantidad": new_cantidad})
            event = EditEvent(
                kind="guia_line_edit",
                # B2: persist the line selector so restore_from_sidecar can replay it.
                target={
                    "guia_id": guia_id,
                    "line_index": line_index,
                    "material_canonical": material_canonical,
                },
                field="cantidad",
                old_value=str(old_cantidad),
                new_value=str(new_cantidad),
            )

        lines[resolved_index] = new_line
        updated_guia = guia.model_copy(update={"lines": lines})

        # Mutate in-memory state
        new_guias = list(self._guias)
        new_guias[target_idx] = updated_guia
        self._guias = new_guias

        # Recompute all rows after the edit (carry the SUNAT delivery floor so the
        # crossed-bounds protection is not lost on the review path).
        self._rows = self._reconciler.reconcile(
            self._declared, self._guias, delivery_dates=self._delivery_dates()
        )

        self._audit_trail.append(event)
        self._persist()

        return list(self._rows)

    def apply_reassignment(
        self,
        guia_id: str,
        new_registro: str,
        new_fecha: Any,
    ) -> list[ReconciliationRow]:
        """Reassign a guía to a different registro/fecha and recompute rows.

        Delegates the pure list transformation to ReconciliationService.apply_reassignment,
        then re-runs reconcile over the updated guías.

        Args:
            guia_id:      Identifier of the guía to reassign.
            new_registro: Target registro number.
            new_fecha:    Target reception date (``date``, ISO-8601 str, or None).

        Returns:
            Updated list of reconciliation rows.

        Raises:
            ValueError: If guia_id is not found.
        """
        # B4: reject a Contents/section ID masquerading as a Registro N°.
        if is_section_id(new_registro):
            raise ValueError(
                f"{new_registro!r} is a Contents/section ID, not a valid Registro N°. "
                "Three-identifier invariant: Contents-ID != Registro N° != QR serie-numero."
            )

        # Verify the guía exists before delegating
        self._find_guia_index(guia_id)

        guia_before = next(g for g in self._guias if g.guia_id == guia_id)
        parsed_date = _parse_date(new_fecha)

        # B6: idempotent — skip the audit event + persist when nothing changes
        # (double-click, or replay on every restart would otherwise inflate the trail).
        if guia_before.registro == new_registro and guia_before.fecha == parsed_date:
            return list(self._rows)

        event = EditEvent(
            kind="reassignment",
            target={"guia_id": guia_id},
            field=None,
            old_value={"registro": guia_before.registro, "fecha": str(guia_before.fecha)},
            new_value={"registro": new_registro, "fecha": str(parsed_date)},
        )

        # Pure domain transformation
        self._guias = self._reconciler.apply_reassignment(
            self._guias,
            guia_id=guia_id,
            new_registro=new_registro,
            new_fecha=parsed_date,
        )

        # Re-run full reconcile (carry the SUNAT delivery floor so the crossed-bounds
        # protection survives reassignment — the primary R9 misfiled-guía workflow).
        self._rows = self._reconciler.reconcile(
            self._declared, self._guias, delivery_dates=self._delivery_dates()
        )

        # Audit + persist
        self._audit_trail.append(event)
        self._persist()

        return list(self._rows)

    def add_recovered_guia(
        self,
        guia: GuiaDeRemision,
    ) -> list[ReconciliationRow]:
        """Append a recovered guía and remove its ErroredGuia entry; re-reconcile.

        T-3 / REV-R05: this is the SOLE ReviewService mutation hook for REINTENTAR
        recovery.  Only accepts guías whose lines all have ``requires_review=True``
        (invariant — reconciliation validation gate; recovered guías are never
        auto-accepted).

        Sequence:
          1. Resolve replace-vs-idempotent-vs-append against the REAL precondition.
             The pipeline persists each errored block as a 0-line GuiaDeRemision that
             IS already in ``_guias`` (``errored_guias`` is a PARALLEL side-channel for
             the same guia_id).  So a guia_id match does NOT imply genuine idempotency:
               - existing guía already recovered (``len(lines) > 0``)  → TRUE idempotency,
                 no-op, return current rows (no audit event, no double-add).
               - existing guía is the 0-line PLACEHOLDER (``len(lines) == 0``) → REPLACE
                 it with the with-lines recovered guía.
               - no existing guía with that guia_id → append (transient-error path).
          2. Drop matching guia_id from _errored_guias.
          3. Re-reconcile via _reconciler.reconcile with current _delivery_dates().
          4. Emit ``recovered_guia`` EditEvent to the audit trail.
          5. _persist().

        Args:
            guia: A GuiaDeRemision built by ReprocessService (all lines requires_review=True).

        Returns:
            Updated list of reconciliation rows after re-reconcile.
        """
        # Fail-closed guard (FIX #5): the auto-reject invariant — recovered lines
        # ALWAYS requires_review=True (reconciliation validation gate) — must not
        # rest solely on callers.  Reject any line that would bypass review.
        bad = [ln for ln in guia.lines if ln.requires_review is not True]
        if bad:
            raise ValueError(
                f"add_recovered_guia: guía {guia.guia_id!r} has "
                f"{len(bad)} line(s) with requires_review!=True — recovered guías "
                "must always be flagged for review (reconciliation validation gate)."
            )

        # Resolve the existing guía with this guia_id (if any).
        existing_idx: int | None = next(
            (i for i, g in enumerate(self._guias) if g.guia_id == guia.guia_id),
            None,
        )

        if existing_idx is not None and len(self._guias[existing_idx].lines) > 0:
            # TRUE idempotency: a with-lines guía already exists → no mutation.
            return list(self._rows)

        new_guias = list(self._guias)
        if existing_idx is not None:
            # PLACEHOLDER (0-line) case: REPLACE in place so we never duplicate
            # the guia_id and the with-lines version wins for re-reconcile.
            # The placeholder carries the AUTHORITATIVE registro from pipeline
            # assembly (Protocolo linkage); SUNAT-recovered guías have registro=None
            # → inherit it so the recovered material reconciles INTO the registro
            # instead of landing in unresolved (registro=None).
            placeholder = self._guias[existing_idx]
            if guia.registro is None and placeholder.registro is not None:
                guia = guia.model_copy(update={"registro": placeholder.registro})
            new_guias[existing_idx] = guia
        else:
            # Transient-error path: the errored guía was never a 0-line placeholder
            # in _guias → append.
            new_guias.append(guia)
        self._guias = new_guias

        # Remove from errored_guias.
        self._errored_guias = [
            e for e in self._errored_guias if e.guia_id != guia.guia_id
        ]

        # Re-reconcile with the updated guía list.
        self._rows = self._reconciler.reconcile(
            self._declared, self._guias, delivery_dates=self._delivery_dates()
        )

        # Audit event (new kind: "recovered_guia").
        event = EditEvent(
            kind="recovered_guia",
            target={"guia_id": guia.guia_id},
            field=None,
            old_value=None,
            new_value=guia.model_dump(mode="json"),
        )
        self._audit_trail.append(event)
        self._persist()

        return list(self._rows)

    def mark_retry_attempted(self, guia_id: str) -> None:
        """Durably flag a FAILED REINTENTAR on the matching errored guía (FIX 1).

        When ``ReprocessService.apply_retry`` returns ``recovered=False`` the guía
        stays errored.  Flipping ``ErroredGuia.retry_attempted`` to ``True`` is the
        SOLE signal the frontend uses to gate the REINTENTAR button + show the
        "SUNAT no disponible" hint (and, downstream, the "Reprocesar con IA" button).

        ``ErroredGuia`` is a pydantic model → replaced via ``model_copy`` (immutable
        update).  A ``retry_attempted`` sidecar event is emitted and replayed in
        ``restore_from_sidecar`` so the flag survives a restart.  This is an additive
        UX side-channel — it NEVER touches the group key, status, delta, or qty of any
        reconciliation row.  Unknown ``guia_id`` is a no-op.
        """
        idx = next(
            (i for i, e in enumerate(self._errored_guias) if e.guia_id == guia_id),
            None,
        )
        if idx is None:
            return  # no-op: guia_id already recovered or never errored

        if self._errored_guias[idx].retry_attempted:
            # Idempotent: already flagged → no duplicate event/persist.
            return

        updated = list(self._errored_guias)
        updated[idx] = updated[idx].model_copy(update={"retry_attempted": True})
        self._errored_guias = updated

        event = EditEvent(
            kind="retry_attempted",
            target={"guia_id": guia_id},
            field=None,
            old_value=None,
            new_value=None,
        )
        self._audit_trail.append(event)
        self._persist()

    # ------------------------------------------------------------------
    # Resumability: restore from sidecar
    # ------------------------------------------------------------------

    @classmethod
    def restore_from_sidecar(
        cls,
        declared: list[Registro],
        guias: list[GuiaDeRemision],
        rows: list[ReconciliationRow],
        ctx: RunContext,
        errored_guias: list[ErroredGuia] | None = None,
    ) -> "ReviewService":
        """Reconstruct a ReviewService by replaying edits from the sidecar.

        This is the restart path.  The sidecar is loaded, and each stored
        event is replayed in order against the freshly-loaded domain objects.

        If the sidecar contains no edits (new run or cleared), an empty
        ReviewService is returned.

        Args:
            declared:      Registro list from the extraction cache.
            guias:         GuiaDeRemision list from the extraction cache.
            rows:          Initial ReconciliationRow list.
            ctx:           RunContext with the sidecar path.
            errored_guias: Guías that resolved to 0 lines (REV-E03).  Hydrated
                           from the extraction cache by build_review_service;
                           constructor state, NOT replayed as edit events.

        Returns:
            A ReviewService with all prior edits already applied.
        """
        service = cls(
            declared=declared,
            guias=guias,
            rows=rows,
            ctx=ctx,
            errored_guias=errored_guias,
        )
        sidecar = ctx.read_review_sidecar()
        edits: list[dict[str, Any]] = sidecar.get("edits", [])

        for edit in edits:
            kind = edit.get("kind")
            target = edit.get("target", {})
            guia_id = target.get("guia_id")
            if guia_id is None:
                continue

            if kind == "field_edit":
                field = edit.get("field")
                new_value = edit.get("new_value")
                try:
                    service.apply_edit(guia_id, field, new_value)
                except (ValueError, ReconciliationError):
                    # Tolerate individual replay errors to avoid blocking restart
                    pass

            elif kind == "guia_line_edit":
                # B2: replay the line-level cantidad edit using the persisted selector.
                from decimal import Decimal, InvalidOperation  # noqa: PLC0415

                line_index = target.get("line_index")
                material_canonical = target.get("material_canonical")
                raw_value = edit.get("new_value")
                try:
                    new_cantidad = Decimal(str(raw_value))
                except (InvalidOperation, TypeError, ValueError):
                    continue
                try:
                    service.apply_guia_line_edit(
                        guia_id,
                        line_index,
                        material_canonical,
                        new_cantidad,
                    )
                except (ValueError, ReconciliationError):
                    pass

            elif kind == "reassignment":
                raw_new = edit.get("new_value", {})
                if isinstance(raw_new, dict):
                    new_registro = raw_new.get("registro", "")
                    new_fecha = raw_new.get("fecha")
                else:
                    # stored as string by older serialisation
                    continue
                try:
                    service.apply_reassignment(guia_id, new_registro, new_fecha)
                except (ValueError, ReconciliationError):
                    pass

            elif kind == "recovered_guia":
                # T-4 (REV-R06): replay a recovered_guia event — re-adds the fully
                # normalized GuiaDeRemision from sidecar JSON without re-fetching.
                # new_value is the model_dump(mode="json") dict written at persist time.
                raw_guia = edit.get("new_value")
                if not isinstance(raw_guia, dict):
                    continue
                try:
                    guia = GuiaDeRemision.model_validate(raw_guia)
                    # R2-W2 (silent-data-loss guard): a recovered_guia event
                    # serialized by an OLDER build may carry lines with
                    # requires_review=False (the historical default). The new
                    # fail-closed guard in add_recovered_guia (FIX #5) would raise
                    # ValueError → swallowed below → the recovered guía would
                    # SILENTLY VANISH on restart. Coerce lines to requires_review=True
                    # BEFORE re-adding: recovered guías are ALWAYS requires_review=True
                    # by contract, so this restores the historical re-add behavior
                    # without masking anything (the guard still protects live callers).
                    if any(ln.requires_review is not True for ln in guia.lines):
                        guia = guia.model_copy(
                            update={
                                "lines": [
                                    ln.model_copy(update={"requires_review": True})
                                    for ln in guia.lines
                                ]
                            }
                        )
                    service.add_recovered_guia(guia)
                except (ValueError, ReconciliationError):
                    # Tolerate replay errors but DO NOT swallow silently — a
                    # dropped recovered guía is a silent-data-loss path (R2-W2).
                    logger.warning(
                        "restore_from_sidecar: failed to replay recovered_guia "
                        "event for guia_id=%r; the recovered guía was not re-added.",
                        guia_id,
                    )

            elif kind == "retry_attempted":
                # FIX 1: replay the failed-retry flag so it survives a restart.
                try:
                    service.mark_retry_attempted(guia_id)
                except (ValueError, ReconciliationError):
                    pass

        return service

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _delivery_dates(self) -> dict[str, date]:
        """SUNAT delivery-floor map (``guia_id`` → ``fecha_entrega``) from the guías.

        The crossed-bounds protection in ``ReconciliationService.reconcile``
        (do NOT clamp below the SUNAT delivery floor when ``fecha_entrega >``
        Protocolo) depends on this map.  ``fecha_entrega`` is persisted ON each
        guía by the pipeline, so it survives the cache round-trip and every review
        re-reconcile.  Empty map when SUNAT is off/unavailable (graceful).
        """
        return {
            g.guia_id: g.fecha_entrega
            for g in self._guias
            if g.fecha_entrega is not None
        }

    def _find_guia_index(self, guia_id: str) -> int:
        """Return the list index of the guía with ``guia_id``, or raise ValueError."""
        for i, g in enumerate(self._guias):
            if g.guia_id == guia_id:
                return i
        raise ValueError(f"GuiaDeRemision with guia_id={guia_id!r} not found.")

    def _persist(self) -> None:
        """Atomically overwrite the review sidecar with current state.

        B3: MERGE into the existing sidecar instead of replacing it wholesale.
        The pipeline writes a ``vision_audit`` key (RunContext.append_vision_audit)
        that carries vision-call provenance; a naive full overwrite on the first
        review mutation permanently dropped it. We read the current sidecar, update
        only the review-owned keys (``edits``/``audit_trail``), and preserve every
        other key (e.g. ``vision_audit`` and any future provenance).
        """
        trail = [e.to_dict() for e in self._audit_trail]
        data: dict[str, Any] = dict(self._ctx.read_review_sidecar())
        data["edits"] = trail
        data["audit_trail"] = trail
        self._ctx.write_review_sidecar(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(value: Any) -> date | None:
    """Coerce a value to ``datetime.date`` or None.

    Accepts:
        - None → None
        - ``datetime.date`` instance → returned as-is
        - ISO-8601 string "YYYY-MM-DD" → parsed
        - Other strings → raises ValueError

    Raises:
        ValueError: On unrecognised string format.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in ("none", "null", ""):
            return None
        try:
            return date.fromisoformat(stripped)
        except ValueError:
            raise ValueError(
                f"Cannot parse date from {value!r}. Expected ISO-8601 format YYYY-MM-DD."
            )
    raise ValueError(f"Expected date, str, or None; got {type(value).__name__}.")
