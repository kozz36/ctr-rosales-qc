"""OCR adapter factory (EXT-027).

Builds the correct :class:`~reconciliation.domain.ports.ExtractionPort`
implementation from :class:`~reconciliation.application.config.OcrConfig`.

This factory is the ONLY module that imports concrete OCR adapters.  The
domain and application layers depend only on ``ExtractionPort`` (a Protocol).

Strategy pattern — ``build_ocr_extractor(cfg)`` selects the implementation
based on ``cfg.enabled`` and ``cfg.engine``:

- ``enabled=False``             → :class:`NullOcrExtractor` (Null Object)
- ``engine="paddle"``           → :class:`PrintedTableAdapter` (existing)
- ``engine="rapidocr"``         → :class:`RapidOCRAdapter` (SDD#1, PR#2)

All concrete adapters are imported INSIDE the function body so this module
can be imported without pulling in any OCR SDK at module load time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reconciliation.application.config import OcrConfig
    from reconciliation.domain.ports import ExtractionPort


def build_ocr_extractor(cfg: "OcrConfig") -> "ExtractionPort":
    """Construct and return the configured ExtractionPort OCR implementation.

    Args:
        cfg: ``OcrConfig`` sub-config.  ``cfg.enabled`` gates the null path;
             ``cfg.engine`` selects the concrete adapter when enabled.

    Returns:
        A ready-to-use ExtractionPort instance.

    Raises:
        ValueError: if ``cfg.engine`` is not one of the recognised values.
    """
    # Disabled path: inject Null Object — no OCR engine imported or instantiated.
    if not cfg.enabled:
        from reconciliation.adapters.ocr.null_extractor import (  # noqa: PLC0415
            NullOcrExtractor,
        )

        return NullOcrExtractor()

    engine = getattr(cfg, "engine", "paddle")

    if engine == "rapidocr":
        from reconciliation.adapters.ocr.rapid_table import (  # noqa: PLC0415
            RapidOCRAdapter,
        )

        return RapidOCRAdapter()

    if engine == "paddle":
        from reconciliation.adapters.ocr.paddle_table import (  # noqa: PLC0415
            PrintedTableAdapter,
        )

        return PrintedTableAdapter()

    raise ValueError(
        f"Unknown OCR engine: {engine!r}. "
        "Expected one of: 'paddle', 'rapidocr'."
    )
