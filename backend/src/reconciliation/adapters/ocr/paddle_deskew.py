"""DeskewAdapter — orientation correction for guía pages.

Uses PaddleOCR ``DocImgOrientationClassification`` to classify orientation
as 0 / 90 / 180 / 270 degrees and rotate the image to upright.

**Lazy import**: ``paddleocr`` / ``paddlepaddle`` are NOT imported at module
top-level.  The first call to :meth:`correct_orientation` triggers model
load.  This means the package imports cleanly even when PaddleOCR is not
installed — the pipeline can still run when deskew is skipped or mocked.

**Scope**: guía pages only (``DeskewConfig.scope == "guia_only"``).  Pages
classified as DECLARED or IGNORED are never passed to this adapter.
The pipeline handles the fallback: if PaddleOCR is unavailable (import
error), the pipeline continues with the original orientation.

**Orientation fallback** (locked decision EXT-003):
    - On any per-image error: log a warning, return the original bytes.
    - On import / model-load failure: log once, set a ``_unavailable``
      flag so subsequent calls fast-return without retrying the import.

**paddleocr 3.x API**: the classifier is instantiated with
``use_doc_orientation_classify=True`` only; the removed 2.x ``use_angle_cls``,
``use_gpu``, and ``show_log`` are omitted.  ``_classify_angle`` now calls
``predict()`` and reads the angle from
``result[0]["doc_preprocessor_res"]["angle"]`` instead of parsing the
2.x nested ``result[0][0][0][1]`` structure.  ``_run_title_ocr`` reads the
3.x ``rec_texts`` / ``rec_scores`` keys instead of the 2.x ``item[1]``
tuple.  All changes are backward-compatible with injected mock classifiers
via the ``_classifier`` parameter.
"""

from __future__ import annotations

import io
import logging
from threading import Lock

logger = logging.getLogger(__name__)

_INIT_LOCK = Lock()


class DeskewAdapter:
    """Correct the orientation of guía page images using PaddleOCR.

    The underlying model is loaded lazily on the first call to
    :meth:`correct_orientation`.  Instantiating the adapter does NOT import
    PaddleOCR, so the rest of the package imports even without PaddleOCR
    installed.

    Args:
        _classifier: Optional pre-built classifier instance injected for
                     testing.  If provided, the lazy-load path is skipped.
    """

    # Supported rotation angles returned by DocImgOrientationClassification
    _ANGLES = frozenset({0, 90, 180, 270})

    def __init__(
        self,
        _classifier: object | None = None,
    ) -> None:
        # Injected in tests to avoid importing PaddleOCR
        self._classifier = _classifier
        self._unavailable: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_title(self, image: bytes) -> str | None:
        """Extract the document title string from a page image via OCR.

        Used by the pipeline's classify stage to supply an ``ocr_title`` for
        scanned pages (empty digital text) so they can be classified as GUIA,
        DECLARED, etc.

        Returns the first non-empty line that contains a known title keyword,
        or None if OCR is unavailable or no title line is found.

        This is a best-effort operation: any failure returns None without
        propagating an exception.
        """
        if self._unavailable:
            return None

        try:
            classifier = self._get_classifier()
        except Exception:
            self._unavailable = True
            logger.warning("DeskewAdapter.extract_title: PaddleOCR unavailable")
            return None

        try:
            return self._run_title_ocr(image, classifier)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DeskewAdapter.extract_title: OCR failed: %s", exc)
            return None

    def correct_orientation(self, image: bytes) -> bytes:
        """Rotate *image* so that text reads upright.

        Classifies orientation (0/90/180/270°) and applies the inverse
        rotation.  Returns original bytes unchanged on any error.

        Args:
            image: PNG or JPEG bytes of a rendered PDF page.

        Returns:
            PNG bytes with corrected orientation.  On failure, the original
            bytes are returned unmodified (no exception propagates).
        """
        if self._unavailable:
            return image

        try:
            classifier = self._get_classifier()
        except Exception:
            logger.warning(
                "DeskewAdapter: PaddleOCR unavailable — skipping orientation correction"
            )
            self._unavailable = True
            return image

        try:
            angle = self._classify_angle(classifier, image)
            if angle == 0:
                return image
            return self._rotate_image(image, angle)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DeskewAdapter: orientation correction failed: %s", exc)
            return image

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_title_ocr(self, image: bytes, classifier: object) -> str | None:
        """Run text recognition on *image* and return the best title candidate.

        Scans all recognised lines for any known title keyword and returns the
        first match.  Returns None if no known title is found.

        Uses the paddleocr 3.x ``predict()`` API.  The result is a list of
        ``OCRResult`` dict-like objects with ``rec_texts`` and ``rec_scores``
        keys; the 2.x ``ocr(cls=False)`` nested-list format is not produced
        by 3.x.
        """
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        img_array = np.array(img)
        # 3.x: predict() is the canonical API; ocr() delegates to it.
        result = classifier.predict(img_array)  # type: ignore[attr-defined]
        if not result:
            return None

        _TITLE_KEYWORDS = (
            "GUIA DE REMISI",
            "GUÍA DE REMISI",
            "PROTOCOLO DE RECEPCI",
            "PLANILLA RESUMEN",
            "LISTADO DE BARRAS",
        )
        # 3.x result: list of OCRResult, each item["rec_texts"] = list[str]
        for item in result:
            if item is None:
                continue
            try:
                texts = item["rec_texts"]
            except (KeyError, TypeError):
                continue
            if not texts:
                continue
            for text in texts:
                if text is None:
                    continue
                text_upper = str(text).upper()
                for kw in _TITLE_KEYWORDS:
                    if kw in text_upper:
                        return str(text)
        return None

    def _get_classifier(self) -> object:
        """Return the orientation classifier, loading it lazily on first call.

        Uses the paddleocr 3.x API: ``use_doc_orientation_classify=True``
        activates the document orientation classification model.  The removed
        2.x parameters ``use_angle_cls``, ``use_gpu``, and ``show_log`` are
        omitted (device is auto-selected; logging via stdlib).
        ``use_doc_unwarping`` and ``use_textline_orientation`` are explicitly
        disabled to keep instantiation lightweight (orientation only).
        """
        if self._classifier is not None:
            return self._classifier

        with _INIT_LOCK:
            # Double-checked locking: another thread may have loaded it
            if self._classifier is not None:
                return self._classifier

            # Lazy import — fails gracefully if paddleocr not installed
            from paddleocr import PaddleOCR  # noqa: PLC0415

            classifier = PaddleOCR(
                use_doc_orientation_classify=True,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            self._classifier = classifier
            logger.debug("DeskewAdapter: PaddleOCR orientation classifier loaded")
        return self._classifier

    def _classify_angle(self, classifier: object, image: bytes) -> int:
        """Run the orientation classifier and return the detected angle (int).

        Uses the paddleocr 3.x ``predict()`` API.  When the classifier is
        instantiated with ``use_doc_orientation_classify=True``, the pipeline
        performs doc-level orientation classification and stores the detected
        rotation angle (0 / 90 / 180 / 270) in
        ``result[0]["doc_preprocessor_res"]["angle"]``.

        Interpretation: the angle is the image's current rotation from upright.
        We rotate by the NEGATIVE of that angle to correct it.

        The 2.x path (``ocr(cls=True, det=False, rec=False)`` →
        ``result[0][0][0][1]``) is not produced by 3.x; that call signature
        is no longer valid.
        """
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        img_array = np.array(img)

        # 3.x: predict() is the canonical entry point.
        result = classifier.predict(img_array)  # type: ignore[attr-defined]

        if not result:
            return 0

        # 3.x result: list of OCRResult (one per image).
        # Orientation angle is in doc_preprocessor_res["angle"].
        try:
            first = result[0]
            angle_raw = first["doc_preprocessor_res"]["angle"]
            angle = int(angle_raw)
        except (KeyError, TypeError, ValueError, IndexError):
            return 0

        return angle if angle in self._ANGLES else 0

    @staticmethod
    def _rotate_image(image: bytes, angle: int) -> bytes:
        """Rotate *image* by *-angle* degrees (inverse correction) and return PNG bytes."""
        from PIL import Image  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        # expand=True ensures the canvas resizes for 90/270 rotations
        rotated = img.rotate(-angle, expand=True)
        buf = io.BytesIO()
        rotated.save(buf, format="PNG")
        return buf.getvalue()
