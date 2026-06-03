"""MaterialKeyResolver — Strategy pattern for canonical key resolution (R8.3).

Pure domain module — stdlib + Pydantic only. No I/O, no adapter, no SDK.

Spec: MAT-006, MAT-012, ADR-3, ADR-4.

Resolution order (det-first → LLM-fallback → unresolved sentinel):
  1. Call MaterialKeyNormalizer.parse() (deterministic).
     If result non-None → return it directly (method="deterministic").
  2. If inference port is available:
     a. Check per-run cache (keyed by (raw, unidad)).
     b. Call inference.infer(raw) → MaterialKeyInference | None.
     c. Validate result against hallucination guard (canonical diameter + presentacion set).
     d. If valid → build CanonicalKey(method="llm_inferred"), cache, return.
  3. Return CanonicalKey.unresolved(raw, unidad).

Per-run cache (ADR-4):
  dict[(raw, unidad)] → CanonicalKey, populated lazily.
  Only LLM infer() results are cached (deterministic path is cheap).
  One resolver per pipeline run = one cache lifetime.
  NOT on RunContext (purity) nor on the adapter (lifecycle leak).
"""

from __future__ import annotations

from typing import Any

from reconciliation.domain.material_key import CanonicalKey
from reconciliation.domain.material_key_normalizer import (
    CANONICAL_DIAMETERS,
    MaterialKeyNormalizer,
)

# Valid presentacion values — mirrors the normalizer vocabulary
_VALID_PRESENTACION: frozenset[str] = frozenset({"9M", "DOB"})


class MaterialKeyResolver:
    """Strategy: deterministic-first → LLM-fallback → unresolved.

    Args:
        normalizer: MaterialKeyNormalizer instance (deterministic regex).
        inference:  Optional MaterialInferencePort instance.  When None,
                    the resolver operates in deterministic-only mode (safe
                    default for direct-construction tests and air-gap runs).
    """

    def __init__(
        self,
        normalizer: MaterialKeyNormalizer,
        inference: Any | None = None,
    ) -> None:
        self._normalizer = normalizer
        self._inference = inference
        self._cache: dict[tuple[str, str], CanonicalKey] = {}

    def resolve(self, description_raw: str, unidad: str) -> CanonicalKey:
        """Resolve a raw material description to a CanonicalKey.

        Resolution order:
          1. Deterministic regex (no LLM, no cache).
          2. LLM inference (with per-run memoization).
          3. Unresolved sentinel (run continues).

        Args:
            description_raw: Raw material description string.
            unidad:          Unit of measure (KG/TN/RD/Rollo); never converted.

        Returns:
            A CanonicalKey — always non-None (unresolved sentinel on failure).
        """
        # Step 1: deterministic path (cheap — no cache needed)
        det_result = self._normalizer.parse(description_raw, unidad)
        if det_result is not None:
            return det_result

        # Step 2: LLM inference path (only if port available)
        if self._inference is not None:
            cache_key = (description_raw, unidad)

            # Cache hit
            if cache_key in self._cache:
                return self._cache[cache_key]

            # Call inference port
            inference_result = self._inference.infer(description_raw)
            if inference_result is not None:
                # Hallucination guard: validate diameter and presentacion
                diametro = getattr(inference_result, "diametro", None)
                presentacion = getattr(inference_result, "presentacion", None)

                diameter_valid = diametro in CANONICAL_DIAMETERS if diametro else False
                presentacion_valid = presentacion in _VALID_PRESENTACION if presentacion else False

                if diameter_valid and presentacion_valid:
                    canonical = CanonicalKey(
                        familia=getattr(inference_result, "familia", "BARRA"),
                        grado=getattr(inference_result, "grado", None),
                        diametro=diametro,
                        presentacion=presentacion,
                        unidad=unidad,  # type: ignore[arg-type]
                        method="llm_inferred",
                        raw=description_raw,
                    )
                    self._cache[cache_key] = canonical
                    return canonical

        # Step 3: unresolved sentinel
        return CanonicalKey.unresolved(description_raw, unidad)  # type: ignore[arg-type]
