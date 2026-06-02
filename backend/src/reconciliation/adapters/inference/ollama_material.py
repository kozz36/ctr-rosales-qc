"""OllamaMaterialInferenceAdapter — LLM-based material key inference (R8.7, MAT-007).

Implements MaterialInferencePort via Ollama/OpenAI-compatible API.

Design invariants (ADR-2):
- openai is lazy-imported inside infer() — module-level import MUST NOT crash
  if openai is absent.
- Temperature fixed at 0 (deterministic-ish inference).
- <think>...</think> blocks are stripped before JSON parse (MAT-S11 compliance;
  qwen3.5:9b is a thinking model).
- Any exception → return None (graceful degradation, MAT-012).

_SYSTEM_PROMPT mirrors the full LLM inference prompt from the gitignored skill
asset (.claude/skills/material-canonical-matching/assets/llm-inference-prompt.md).
This constant is the repo-tracked copy so the prompt travels with the code.
"""

from __future__ import annotations

import json
import re

from reconciliation.domain.models import MaterialKeyInference

# ---------------------------------------------------------------------------
# System prompt — mirrored from skills asset (gitignored) (R8.7 hard invariant)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT: str = (
    "Eres un experto en materiales de construcción peruanos (acero corrugado / rebar, "
    "norma SUNAT y Aceros Arequipa). Recibes una descripción de material (de un informe "
    "Autodesk Forma O de una guía de remisión electrónica SUNAT) y debes extraer su "
    "identidad canónica. Las descripciones de la misma pieza varían entre fuentes; lo que "
    "identifica al material es: FAMILIA + GRADO + DIÁMETRO + PRESENTACIÓN.\n\n"
    "Reglas:\n"
    '- familia: normalmente "BARRA" (acero corrugado). "acero dimensionado" = BARRA cortada/doblada.\n'
    '- grado: A615/A706 G60 (dual). Normaliza "ag615", "a615/a706 g60", "a a615-g60" -> "A615 G60".\n'
    '- diámetro: uno de 8mm, 3/8", 1/2", 5/8", 3/4", 1", 1 3/8".\n'
    '- presentación: "9M" (varilla recta 9 m, de "x 9m") o "DOB" (doblado/dimensionado, de '
    '"dob"/"dimensionado"/"apl"). Son DISTINTAS; no las unifiques.\n'
    "- NO conviertas unidades (KG/TN/RD/Rollo). Reporta la unidad tal cual.\n"
    "- Si no puedes determinar un campo, devuélvelo null y marca needs_review=true.\n\n"
    "Devuelve SOLO JSON:\n"
    '{"familia":"BARRA","grado":"A615 G60","diametro":"1/2\\"","presentacion":"9M","unidad":"TN","needs_review":false}'
)

# Regex to strip <think>...</think> blocks (including multiline)
_THINK_BLOCK_RE: re.Pattern[str] = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaMaterialInferenceAdapter:
    """Implements MaterialInferencePort via Ollama/OpenAI-compatible text API.

    Lazy-imports openai inside infer() to avoid a hard dependency at module-load time.

    Args:
        model:       Ollama model name (e.g. "qwen3.5:9b").
        base_url:    OpenAI-compatible base URL (e.g. "http://localhost:11434/v1").
        temperature: Inference temperature (spec: 0.0 for deterministic-ish output).
        timeout_s:   HTTP request timeout in seconds.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        temperature: float,
        timeout_s: float,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._temperature = temperature
        self._timeout_s = timeout_s

    def infer(self, description: str) -> MaterialKeyInference | None:
        """Infer canonical key tuple from an ambiguous description.

        Args:
            description: Raw material description string.

        Returns:
            MaterialKeyInference on success, None on any failure.
        """
        try:
            # Lazy-import: openai must NOT be imported at module level
            from openai import OpenAI  # noqa: PLC0415  # type: ignore[import-untyped]

            client = OpenAI(
                base_url=self._base_url,
                api_key="ollama",  # Ollama ignores the key; value is a placeholder
                timeout=self._timeout_s,
            )
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": description},
                ],
                temperature=self._temperature,
            )
            raw_content = response.choices[0].message.content or ""
            return self._parse_response(raw_content)
        except Exception:  # noqa: BLE001
            return None

    def _parse_response(self, raw: str) -> MaterialKeyInference | None:
        """Strip think-blocks and parse JSON into MaterialKeyInference.

        Exposed as a method (not private) so unit tests can exercise the parse
        logic without mocking the entire OpenAI client.

        Args:
            raw: Raw LLM response string (may contain <think> blocks).

        Returns:
            MaterialKeyInference on success, None on any parse/validation failure.
        """
        if not raw:
            return None
        try:
            # Strip <think>...</think> blocks first (MAT-S11)
            stripped = _THINK_BLOCK_RE.sub("", raw).strip()
            data = json.loads(stripped)
            # familia is required; everything else optional
            if "familia" not in data:
                return None
            return MaterialKeyInference(
                familia=data["familia"],
                grado=data.get("grado"),
                diametro=data.get("diametro"),
                presentacion=data.get("presentacion"),
                confidence=float(data.get("confidence", 0.0)),
            )
        except Exception:  # noqa: BLE001
            return None
