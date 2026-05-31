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
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from reconciliation.domain.errors import ReconciliationError
from reconciliation.domain.models import (
    GuiaDeRemision,
    ReconciliationRow,
    Registro,
)
from reconciliation.domain.reconciliation import ReconciliationService
from reconciliation.application.run_context import RunContext


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
    ) -> None:
        self._declared: list[Registro] = list(declared)
        self._guias: list[GuiaDeRemision] = list(guias)
        self._rows: list[ReconciliationRow] = list(rows)
        self._ctx = ctx
        self._reconciler = ReconciliationService()
        self._audit_trail: list[EditEvent] = []

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

        Args:
            guia_id:    Identifier of the target GuiaDeRemision.
            field:      Field name to update (``"fecha"`` or ``"registro"``).
            new_value:  New field value.

        Returns:
            Updated list of reconciliation rows (all rows recomputed).

        Raises:
            ValueError: If the guia_id is not found or field is unsupported.
        """
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

        # Recompute all rows after the edit
        self._rows = self._reconciler.reconcile(self._declared, self._guias)

        # Append audit event and persist
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
        # Verify the guía exists before delegating
        self._find_guia_index(guia_id)

        guia_before = next(g for g in self._guias if g.guia_id == guia_id)
        parsed_date = _parse_date(new_fecha)

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

        # Re-run full reconcile
        self._rows = self._reconciler.reconcile(self._declared, self._guias)

        # Audit + persist
        self._audit_trail.append(event)
        self._persist()

        return list(self._rows)

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
    ) -> "ReviewService":
        """Reconstruct a ReviewService by replaying edits from the sidecar.

        This is the restart path.  The sidecar is loaded, and each stored
        event is replayed in order against the freshly-loaded domain objects.

        If the sidecar contains no edits (new run or cleared), an empty
        ReviewService is returned.

        Args:
            declared: Registro list from the extraction cache.
            guias:    GuiaDeRemision list from the extraction cache.
            rows:     Initial ReconciliationRow list.
            ctx:      RunContext with the sidecar path.

        Returns:
            A ReviewService with all prior edits already applied.
        """
        service = cls(declared=declared, guias=guias, rows=rows, ctx=ctx)
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

        return service

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_guia_index(self, guia_id: str) -> int:
        """Return the list index of the guía with ``guia_id``, or raise ValueError."""
        for i, g in enumerate(self._guias):
            if g.guia_id == guia_id:
                return i
        raise ValueError(f"GuiaDeRemision with guia_id={guia_id!r} not found.")

    def _persist(self) -> None:
        """Atomically overwrite the review sidecar with current state."""
        data: dict[str, Any] = {
            "edits": [e.to_dict() for e in self._audit_trail],
            "audit_trail": [e.to_dict() for e in self._audit_trail],
        }
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
