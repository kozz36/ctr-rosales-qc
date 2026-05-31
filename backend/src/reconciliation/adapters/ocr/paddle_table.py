"""PrintedTableAdapter — OCR-based material line extraction for guía pages.

Uses PaddleOCR text recognition to extract material lines from printed
(non-digital) guía pages.  Implements the :class:`ExtractionPort` ``extract_printed_table``
method.

**Lazy import**: ``paddleocr`` / ``paddlepaddle`` are NOT imported at module
top-level so the package imports cleanly without PaddleOCR installed.

**Confidence threshold**: 0.85 (locked, EXT-002).  Lines with any per-value
confidence below 0.85 have ``requires_review=True`` set on the returned
:class:`MaterialLine` and are not silently discarded.

**Error isolation**: on any extraction error, raises
:class:`~reconciliation.domain.errors.ExtractionError` rather than
propagating raw OCR exceptions.  The pipeline catches ExtractionError and
treats the page as unextractable (returns empty list, flags for review).
"""

from __future__ import annotations

import io
import logging
import re
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Final

from reconciliation.domain.errors import ExtractionError
from reconciliation.domain.models import MaterialLine
from reconciliation.domain.normalizer import MaterialNormalizer

logger = logging.getLogger(__name__)

_INIT_LOCK = Lock()

# Locked threshold — EXT-002
_CONFIDENCE_THRESHOLD: Final[float] = 0.85

# Units recognised in guía printed tables
_UNIT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(TN|KG|RD|Rollo)\b", re.IGNORECASE
)

# Material line pattern: "<description> <qty> <unit>"
# Flexible: description may contain spaces/slashes; qty uses . or ,
_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(.+?)\s+(\d+(?:[.,]\d+)?)\s+(TN|KG|RD|Rollo)\s*$",
    re.IGNORECASE,
)

_NORMALIZER: Final[MaterialNormalizer] = MaterialNormalizer()


class PrintedTableAdapter:
    """Extract material lines from a printed guía page using PaddleOCR.

    Implements the OCR path of :class:`~reconciliation.domain.ports.ExtractionPort`.

    ``extract_declared`` is a no-op — declared side is handled by
    :class:`~reconciliation.adapters.pdf.digital_text_extractor.DigitalTextExtractionAdapter`.

    Args:
        use_gpu: Whether PaddleOCR should use GPU.  Defaults to False.
        _ocr: Optional pre-built PaddleOCR instance injected for testing.
              When provided, the lazy-load path is skipped entirely.
    """

    def __init__(
        self,
        use_gpu: bool = False,
        _ocr: object | None = None,
    ) -> None:
        self._use_gpu = use_gpu
        self._ocr = _ocr
        self._unavailable: bool = False

    # ------------------------------------------------------------------
    # ExtractionPort interface
    # ------------------------------------------------------------------

    def extract_declared(self, text: str) -> list[MaterialLine]:  # noqa: ARG002
        """No-op — digital declared extraction is handled by DigitalTextExtractionAdapter."""
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        """Extract material lines from *image* using OCR.

        Lines with any recognised per-character confidence below 0.85 are
        returned with ``requires_review=True`` (they are NOT dropped).

        Args:
            image: PNG or JPEG bytes of a rendered guía page.

        Returns:
            List of :class:`MaterialLine`.  Empty list if no material lines
            are detected.

        Raises:
            ExtractionError: if OCR fails with an unrecoverable error.
        """
        if self._unavailable:
            raise ExtractionError("PrintedTableAdapter: PaddleOCR is unavailable")

        try:
            ocr = self._get_ocr()
        except Exception as exc:
            logger.warning(
                "PrintedTableAdapter: PaddleOCR unavailable — %s", exc
            )
            self._unavailable = True
            raise ExtractionError(
                f"PrintedTableAdapter: PaddleOCR could not be loaded: {exc}"
            ) from exc

        try:
            return self._run_ocr(ocr, image)
        except ExtractionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(
                f"PrintedTableAdapter: OCR failed on image: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_ocr(self) -> object:
        """Return the PaddleOCR instance, loading it lazily on first call."""
        if self._ocr is not None:
            return self._ocr

        with _INIT_LOCK:
            if self._ocr is not None:
                return self._ocr

            from paddleocr import PaddleOCR  # type: ignore[import]  # noqa: PLC0415

            ocr = PaddleOCR(
                use_angle_cls=True,
                use_gpu=self._use_gpu,
                show_log=False,
                lang="es",
            )
            self._ocr = ocr
            logger.debug("PrintedTableAdapter: PaddleOCR recognition engine loaded")
        return self._ocr  # type: ignore[return-value]

    def _run_ocr(self, ocr: object, image: bytes) -> list[MaterialLine]:
        """Run OCR on *image* and parse results into MaterialLine objects."""
        import numpy as np  # type: ignore[import]  # noqa: PLC0415
        from PIL import Image  # type: ignore[import]  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        img_array = np.array(img)

        result = ocr.ocr(img_array, cls=True)  # type: ignore[union-attr]

        if not result or not result[0]:
            return []

        # Gather all text lines from OCR result.
        # Each element in result[0]: [[bbox_pts], (text, confidence)]
        raw_lines: list[tuple[str, float]] = []
        for item in result[0]:
            if not item or len(item) < 2:
                continue
            text_conf = item[1]
            if not text_conf or len(text_conf) < 2:
                continue
            text: str = text_conf[0]
            conf: float = float(text_conf[1])
            raw_lines.append((text.strip(), conf))

        return self._parse_lines(raw_lines)

    def _parse_lines(
        self, raw_lines: list[tuple[str, float]]
    ) -> list[MaterialLine]:
        """Parse (text, confidence) pairs into MaterialLine objects.

        Strategy: try to match each text line against the material line
        grammar.  Non-matching lines (headers, dates, page numbers) are
        silently skipped.
        """
        lines: list[MaterialLine] = []

        for text, conf in raw_lines:
            mat = _LINE_RE.match(text)
            if not mat:
                continue

            desc_raw = mat.group(1).strip()
            qty_str = mat.group(2).strip().replace(",", ".")
            unit_raw = mat.group(3).strip()

            unit = unit_raw.upper()
            unit_literal = "Rollo" if unit == "ROLLO" else unit
            if unit_literal not in {"TN", "KG", "RD", "Rollo"}:
                continue

            try:
                cantidad = Decimal(qty_str)
            except InvalidOperation:
                continue

            requires_review = conf < _CONFIDENCE_THRESHOLD

            lines.append(
                MaterialLine(
                    description_raw=desc_raw,
                    description_canonical=_NORMALIZER.canonicalize(desc_raw),
                    unidad=unit_literal,  # type: ignore[arg-type]
                    cantidad=cantidad,
                    confidence=conf,
                    requires_review=requires_review,
                )
            )

        return lines
