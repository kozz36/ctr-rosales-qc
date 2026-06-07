"""RapidOCRAdapter — OCR-based material line extraction using RapidOCR (EXT-028 + EXT-030).

Uses RapidOCR PP-OCRv5-server (ONNX, paddle-free) to detect text boxes on guía
pages, then delegates row parsing to the pure :mod:`box_row_parser` module.

**Lazy imports**: ``rapidocr``, ``onnxruntime``, ``numpy``, and ``PIL`` are NEVER
imported at module top-level.  They are imported INSIDE methods on first use.
The test suite (and the rest of the pipeline) MUST run with those packages
uninstalled — import purity is enforced by ``test_lazy_import_not_triggered_at_init``.

**Injected engine seam**: the constructor accepts an optional ``_engine``
parameter.  When provided it is used directly (bypassing real engine construction);
this lets unit tests inject a mock returning a pre-defined result without
installing rapidocr or onnxruntime.  Mirrors the ``_ocr`` injection idiom in
:class:`~reconciliation.adapters.ocr.paddle_table.PrintedTableAdapter`.

**Engine lifecycle**: the real RapidOCR instance (~165 MB of ONNX weights) is
constructed ONCE per adapter instance and cached on ``self._engine`` using a
double-checked lock, NOT per ``extract_printed_table`` call.

**Self-scoring orientation** (EXT-030 / Design §6):
  1. Rotate the image -90° (default — guías are typically scanned portrait-left).
  2. Run OCR + ``parse_box_rows``.
  3. If 0 valid rows → retry the four cardinal orientations {0°, 90°, 180°, 270°}.
  4. Pick the orientation with the MOST valid rows.

This is an adapter concern (I/O side-effectful rotation); the domain parser
(:func:`~box_row_parser.parse_box_rows`) remains pure.

**Graceful degradation**: any engine exception during inference is caught and
logged; ``extract_printed_table`` returns ``[]`` (never raises).  Empty result
→ guía quantities are empty → MISMATCH flagged for human review (domain rule).

**RapidOCR 3.8.x API** (confirmed):
    from rapidocr import RapidOCR, OCRVersion, ModelType
    engine = RapidOCR(params={
        "Det.ocr_version": OCRVersion.PPOCRV5,
        "Det.model_type": ModelType.SERVER,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.SERVER,
    })
    result = engine(img_array)   # result.boxes / result.txts / result.scores

``result.boxes`` is a ``np.ndarray`` of shape ``(N, 4, 2)`` (float64) — N
4-point polygons (NOT a Python list).  ``result.txts`` is a ``tuple[str]`` and
``result.scores`` is a ``tuple[float]`` (parallel, length N).  All three are
``Optional`` (``None`` when the engine recognises nothing).  Confirmed against
rapidocr 3.8.x ``rapidocr/utils/output.py::RapidOCROutput``.
"""

from __future__ import annotations

import logging
from threading import Lock

from reconciliation.domain.models import MaterialLine

logger = logging.getLogger(__name__)

_INIT_LOCK = Lock()

# Rotation angles tried during the orientation-scoring retry loop (EXT-030).
# The adapter first tries -90° (default for portrait-left guías), then
# retries these four angles if -90° yields 0 valid rows.
_RETRY_ANGLES: tuple[int, ...] = (0, 90, 180, 270)


class RapidOCRAdapter:
    """Extract material lines from a guía page image using RapidOCR PP-OCRv5.

    Implements :class:`~reconciliation.domain.ports.ExtractionPort` for the
    OCR path.  ``extract_declared`` is a no-op.

    Args:
        dpi:     Render DPI of the page image (passed to ``parse_box_rows``).
                 Default 200 matches ``pipeline.py:813`` (fixed constant).
        _engine: Optional pre-built RapidOCR instance injected for testing.
                 When provided the lazy-load path is skipped entirely.
        _parser: Optional ``parse_box_rows`` callable injected for testing.
                 Defaults to ``box_row_parser.parse_box_rows``.
    """

    def __init__(
        self,
        dpi: int = 200,
        _engine: object | None = None,
        _parser: object | None = None,
        _rotate_fn: object | None = None,
    ) -> None:
        self._dpi = dpi
        # Injected seams (tests pass fakes here).
        # When None, the real objects are constructed / called lazily on first use.
        self._engine = _engine
        self._parser = _parser
        # _rotate_fn: optional callable(image_bytes, angle) → numpy ndarray.
        # When injected (tests), bypasses PIL entirely so test images don't need
        # to be valid PNG/JPEG.  The return value is passed directly to the engine.
        self._rotate_fn = _rotate_fn

    # ------------------------------------------------------------------
    # ExtractionPort interface
    # ------------------------------------------------------------------

    def extract_declared(self, text: str) -> list[MaterialLine]:  # noqa: ARG002
        """No-op — declared extraction is handled by DigitalTextExtractionAdapter."""
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        """Extract material lines from *image* using RapidOCR.

        Applies self-scoring orientation: tries -90° first; if 0 valid rows
        are parsed, retries {0°, 90°, 180°, 270°} and picks the best.

        **Graceful degradation**: any engine exception returns [] (never raises).

        Args:
            image: PNG or JPEG bytes of a rendered guía page.

        Returns:
            List of :class:`MaterialLine`.  Empty list on failure or no match.
        """
        try:
            return self._extract_with_orientation(image)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RapidOCRAdapter: inference error (%s); "
                "guía quantities will be empty for this page",
                exc,
            )
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_engine(self) -> object:
        """Return the RapidOCR engine instance, constructing it lazily on first call.

        Double-checked lock pattern (same as PrintedTableAdapter._get_ocr).
        The engine (~165 MB ONNX weights) is constructed ONCE per adapter
        instance and cached on ``self._engine``.

        Raises:
            RuntimeError: if the real engine cannot be constructed (e.g. rapidocr
                          not installed).  Callers should catch broadly.
        """
        if self._engine is not None:
            return self._engine

        with _INIT_LOCK:
            if self._engine is not None:
                return self._engine

            # LAZY import of rapidocr — NEVER at module top level.
            from rapidocr import ModelType, OCRVersion, RapidOCR  # noqa: PLC0415

            self._engine = RapidOCR(
                params={
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.model_type": ModelType.SERVER,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.model_type": ModelType.SERVER,
                }
            )
            logger.debug("RapidOCRAdapter: RapidOCR PP-OCRv5-server engine loaded")

        return self._engine

    def _get_parser(self):  # type: ignore[return]
        """Return the parse_box_rows callable (injected or real)."""
        if self._parser is not None:
            return self._parser
        from reconciliation.adapters.ocr.box_row_parser import (  # noqa: PLC0415
            parse_box_rows,
        )

        return parse_box_rows

    def _count_valid_rows(self, cells: list) -> int:
        """Count valid rows in *cells* using the orientation oracle."""
        from reconciliation.adapters.ocr.box_row_parser import (  # noqa: PLC0415
            count_valid_rows,
        )

        return count_valid_rows(cells, self._dpi)

    def _run_engine(self, img_array: object) -> list:
        """Run the RapidOCR engine on *img_array* and return a list of Cell objects.

        Converts ``result.boxes`` (``np.ndarray`` of shape ``(N, 4, 2)``) +
        ``result.txts`` (``tuple[str]``) + ``result.scores`` (``tuple[float]``)
        into the :class:`~box_row_parser.Cell` shape the parser expects.

        The centroid (cx, cy) of each polygon is derived from the mean of the
        4 corner points (real polygon centroid, NOT a bounding-box midpoint).

        **numpy-agnostic** (C1): ``result.boxes`` is a numpy array at runtime,
        so a truthiness test (``not result.boxes``) raises ``ValueError`` on a
        multi-element array. The guard uses an explicit ``None`` / ``len() == 0``
        check, and the centroid is computed with PLAIN PYTHON over the 4 points
        so it works whether each box/point is a numpy array OR a list — numpy is
        NEVER imported here (it stays lazy/optional, only in :meth:`_rotate`).
        """
        from reconciliation.adapters.ocr.box_row_parser import Cell  # noqa: PLC0415

        engine = self._get_engine()
        result = engine(img_array)

        if result is None or result.boxes is None or len(result.boxes) == 0:
            return []
        # txts/scores are Optional and parallel to boxes; if either is absent
        # while boxes are present there is no recognised text to pair → degrade.
        if result.txts is None or result.scores is None:
            return []

        cells: list[Cell] = []
        for poly, text, conf in zip(result.boxes, result.txts, result.scores):
            if text is None:
                continue
            text_s = str(text).strip()
            if not text_s:
                continue
            # Compute real centroid from the 4-point polygon corners.
            # poly is a (4, 2) array-like with float64 values.
            xs = [float(pt[0]) for pt in poly]
            ys = [float(pt[1]) for pt in poly]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            cells.append(
                Cell(
                    polygon=[(xs[i], ys[i]) for i in range(len(xs))],
                    text=text_s,
                    conf=float(conf),
                    cx=cx,
                    cy=cy,
                )
            )

        return cells

    def _rotate(self, image: bytes, angle: int) -> object:
        """Rotate *image* by *angle* degrees and return a numpy array.

        Uses Pillow (lazy import).  Positive angle = counter-clockwise
        (PIL convention: ``Image.rotate(angle)``).

        Args:
            image: PNG/JPEG bytes.
            angle: Rotation in degrees (counter-clockwise).  0 = no rotation,
                   -90 = rotate 90° clockwise (default guía orientation fix).

        Returns:
            A numpy ``ndarray`` (H × W × 3) suitable for the RapidOCR engine.
        """
        import io  # noqa: PLC0415

        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        if angle != 0:
            # expand=True so the canvas fits the rotated content without cropping.
            img = img.rotate(angle, expand=True)
        return np.array(img)

    def _to_array(self, image: bytes, angle: int) -> object:
        """Convert *image* to a numpy array at the given rotation angle.

        Uses the injected ``_rotate_fn`` when present (test seam: avoids PIL
        dependency on synthetic / partial-header test images).  Falls back to
        the real :meth:`_rotate` implementation otherwise.
        """
        if self._rotate_fn is not None:
            return self._rotate_fn(image, angle)
        return self._rotate(image, angle)

    def _extract_with_orientation(self, image: bytes) -> list[MaterialLine]:
        """Run OCR with self-scoring orientation selection (EXT-030 / Design §6).

        Algorithm:
          1. Try default rotation -90° (guías are typically scanned portrait-left).
          2. Parse cells → rows.
          3. If rows > 0 → return immediately (fast path).
          4. Otherwise retry all four cardinal angles {0, 90, 180, 270}.
          5. Pick the angle with the most valid rows; return those rows.
             Tie-break: lowest index in _RETRY_ANGLES (deterministic).
          6. If no angle yields rows → return [].
        """
        parse = self._get_parser()

        # Step 1-3: default -90° first.
        img_array = self._to_array(image, -90)
        cells = self._run_engine(img_array)
        rows = parse(cells, self._dpi)
        if rows:
            return rows

        # Step 4-5: retry all four cardinal angles, pick best.
        best_rows: list[MaterialLine] = []
        best_count = 0

        for angle in _RETRY_ANGLES:
            img_array = self._to_array(image, angle)
            cells = self._run_engine(img_array)
            rows = parse(cells, self._dpi)
            count = len(rows)
            if count > best_count:
                best_count = count
                best_rows = rows

        return best_rows
