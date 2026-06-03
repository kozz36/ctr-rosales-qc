"""PrintedTableAdapter — OCR-based material line extraction for guía pages.

Uses PaddleOCR text recognition to extract material lines from printed
(non-digital) guía pages.  Implements the :class:`ExtractionPort` ``extract_printed_table``
method.

**Lazy import**: ``paddleocr`` / ``paddlepaddle`` are NOT imported at module
top-level so the package imports cleanly without PaddleOCR installed.

**Confidence threshold**: 0.85 (locked, EXT-002).  Lines with any per-value
confidence below 0.85 have ``requires_review=True`` set on the returned
:class:`MaterialLine` and are not silently discarded.

**Error isolation (graceful degradation)**: on any OCR load or inference
error, ``extract_printed_table`` logs a warning and returns an empty list
instead of raising.  The ``_ocr_failed`` flag is set so callers can detect
that OCR was skipped for this page (guía quantities will be empty → MISMATCH
flagged for human review, which is the correct domain behaviour).

**paddleocr 3.x API**: instantiation uses ``use_textline_orientation=True``
instead of the removed 2.x ``use_angle_cls``.  ``use_gpu`` and ``show_log``
are also removed (3.x auto-selects device; logging is controlled via the
standard Python logging hierarchy).  Result parsing targets the 3.x
``predict()`` output: a list of ``OCRResult`` dict-like objects, each with
``rec_texts`` (list[str]) and ``rec_scores`` (list[float]) keys, rather than
the 2.x nested ``[[ [bbox], (text, conf) ]]`` structure.
"""

from __future__ import annotations

import io
import logging
import re
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Final

from reconciliation.adapters.ocr._capability import is_persistent_capability_failure
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
        _ocr: Optional pre-built PaddleOCR instance injected for testing.
              When provided, the lazy-load path is skipped entirely.
    """

    def __init__(
        self,
        _ocr: object | None = None,
    ) -> None:
        self._ocr = _ocr
        self._unavailable: bool = False
        # Set to True on each call where OCR load/predict fails; callers that
        # need to detect per-page degradation can inspect this flag.
        self._ocr_failed: bool = False

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

        **Graceful degradation**: on any OCR load or inference failure, logs a
        warning, sets ``self._ocr_failed = True``, and returns an empty list.
        The pipeline treats empty OCR output as a guía with no quantities →
        MISMATCH flagged for human review.  The run never aborts due to OCR
        errors (domain rule: flag mismatches, never fail the run).

        Args:
            image: PNG or JPEG bytes of a rendered guía page.

        Returns:
            List of :class:`MaterialLine`.  Empty list when no material lines
            are detected OR when OCR is unavailable / failed.
        """
        self._ocr_failed = False

        if self._unavailable:
            logger.warning("PrintedTableAdapter: PaddleOCR is unavailable; returning empty")
            self._ocr_failed = True
            return []

        try:
            ocr = self._get_ocr()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PrintedTableAdapter: PaddleOCR could not be loaded (%s); "
                "guía quantities will be empty for this page",
                exc,
            )
            self._unavailable = True
            self._ocr_failed = True
            return []

        try:
            return self._run_ocr(ocr, image)
        except Exception as exc:  # noqa: BLE001
            self._ocr_failed = True
            if is_persistent_capability_failure(exc):
                # Structural failure (oneDNN/PIR): predict() will fail for EVERY
                # page.  Mark unavailable so subsequent pages short-circuit at the
                # `if self._unavailable` guard instead of retrying full inference.
                self._unavailable = True
                logger.warning(
                    "PrintedTableAdapter: PaddleOCR inference is structurally "
                    "unavailable (%s); disabling OCR for the remainder of the run",
                    exc,
                )
            else:
                logger.warning(
                    "PrintedTableAdapter: OCR predict failed (%s); "
                    "guía quantities will be empty for this page",
                    exc,
                )
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_ocr(self) -> object:
        """Return the PaddleOCR instance, loading it lazily on first call.

        Uses the paddleocr 3.x API: ``use_textline_orientation`` replaces the
        removed 2.x ``use_angle_cls``.  ``use_gpu`` and ``show_log`` are not
        accepted by 3.x (device is auto-selected; logging via stdlib).
        ``lang="es"`` maps to the Latin-script recognition model (valid in 3.x).
        """
        if self._ocr is not None:
            return self._ocr

        with _INIT_LOCK:
            if self._ocr is not None:
                return self._ocr

            from paddleocr import PaddleOCR  # noqa: PLC0415

            ocr = PaddleOCR(
                use_textline_orientation=True,
                lang="es",
            )
            self._ocr = ocr
            logger.debug("PrintedTableAdapter: PaddleOCR recognition engine loaded")
        return self._ocr

    def _run_ocr(self, ocr: object, image: bytes) -> list[MaterialLine]:
        """Run OCR on *image* and parse results into MaterialLine objects.

        Targets the paddleocr 3.x ``predict()`` API.  The result is a list of
        ``OCRResult`` dict-like objects (one per input image).  Each item
        exposes:
          - ``item["rec_texts"]``: list[str] — recognised text per line
          - ``item["rec_scores"]``: list[float] — per-line confidence
          - ``item["rec_polys"]``: list[ndarray] — bounding polygon per line

        The 2.x ``ocr(img_array, cls=True)`` call returned a deeply-nested
        ``[[ [bbox], (text, conf) ]]`` structure; that format is no longer
        produced by 3.x.
        """
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        img_array = np.array(img)

        # 3.x primary API is predict(); ocr() is a deprecated alias that calls predict().
        result = ocr.predict(img_array)  # type: ignore[attr-defined]

        if not result:
            return []

        # Gather all text lines from 3.x OCR result.
        # result is a list of OCRResult (one per image).  For a single image
        # there is exactly one item; iterate defensively in case of batching.
        raw_lines: list[tuple[str, float]] = []
        for item in result:
            if item is None:
                continue
            try:
                texts = item["rec_texts"]
                scores = item["rec_scores"]
            except (KeyError, TypeError):
                # Defensive: if the result structure is unexpected, skip silently.
                continue
            if not texts:
                continue
            for text, conf in zip(texts, scores):
                if text is None:
                    continue
                raw_lines.append((str(text).strip(), float(conf)))

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
