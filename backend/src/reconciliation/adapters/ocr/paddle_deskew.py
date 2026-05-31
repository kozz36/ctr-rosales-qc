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
        use_gpu: Whether to run PaddleOCR on GPU.  Defaults to False for
                 predictable behaviour in CI/test environments.
        _classifier: Optional pre-built classifier instance injected for
                     testing.  If provided, the lazy-load path is skipped.
    """

    # Supported rotation angles returned by DocImgOrientationClassification
    _ANGLES = frozenset({0, 90, 180, 270})

    def __init__(
        self,
        use_gpu: bool = False,
        _classifier: object | None = None,
    ) -> None:
        self._use_gpu = use_gpu
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
        """
        import numpy as np  # type: ignore[import]  # noqa: PLC0415
        from PIL import Image  # type: ignore[import]  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        img_array = np.array(img)
        # rec=True, det=True for full OCR; cls=False (orientation already corrected)
        result = classifier.ocr(img_array, cls=False)  # type: ignore[union-attr]
        if not result or not result[0]:
            return None

        _TITLE_KEYWORDS = (
            "GUIA DE REMISI",
            "GUÍA DE REMISI",
            "PROTOCOLO DE RECEPCI",
            "PLANILLA RESUMEN",
            "LISTADO DE BARRAS",
        )
        for line in result[0]:
            if not line or len(line) < 2:
                continue
            text_conf = line[1]
            if not text_conf or len(text_conf) < 1:
                continue
            text = str(text_conf[0]).upper()
            for kw in _TITLE_KEYWORDS:
                if kw in text:
                    return text_conf[0]
        return None

    def _get_classifier(self) -> object:
        """Return the orientation classifier, loading it lazily on first call."""
        if self._classifier is not None:
            return self._classifier

        with _INIT_LOCK:
            # Double-checked locking: another thread may have loaded it
            if self._classifier is not None:
                return self._classifier

            # Lazy import — fails gracefully if paddleocr not installed
            from paddleocr import PaddleOCR  # type: ignore[import]  # noqa: PLC0415

            classifier = PaddleOCR(
                use_angle_cls=True,
                use_gpu=self._use_gpu,
                show_log=False,
                use_doc_orientation_classify=True,
            )
            self._classifier = classifier
            logger.debug("DeskewAdapter: PaddleOCR orientation classifier loaded")
        return self._classifier  # type: ignore[return-value]

    def _classify_angle(self, classifier: object, image: bytes) -> int:
        """Run the orientation classifier and return the detected angle (int).

        PaddleOCR ``ocr()`` with ``use_angle_cls=True`` returns results that
        include classification in ``result[0][0][0][1]`` when called on an
        image array.  We use the simpler approach: call the classifier with
        ``cls=True`` on the image bytes wrapped in a PIL Image, then read the
        ``cls_res`` field.

        Interpretation: PaddleOCR returns the TEXT orientation label
        ("0", "90", "180", "270"), meaning the text in the image is rotated
        by that angle.  We rotate by the NEGATIVE of that angle to correct.
        """
        import numpy as np  # type: ignore[import]  # noqa: PLC0415
        from PIL import Image  # type: ignore[import]  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        img_array = np.array(img)

        result = classifier.ocr(img_array, cls=True, det=False, rec=False)  # type: ignore[union-attr]

        if (
            result is None
            or not result
            or not result[0]
        ):
            return 0

        # result[0] is a list of [bbox, (text_or_cls, confidence)]
        # When cls=True, det=False, rec=False: result[0][0] = [None, ('0', conf)]
        first = result[0][0]
        if not first or len(first) < 2:
            return 0

        label_conf = first[1]
        if not label_conf or len(label_conf) < 1:
            return 0

        try:
            angle = int(label_conf[0])
        except (ValueError, TypeError):
            return 0

        return angle if angle in self._ANGLES else 0

    @staticmethod
    def _rotate_image(image: bytes, angle: int) -> bytes:
        """Rotate *image* by *-angle* degrees (inverse correction) and return PNG bytes."""
        from PIL import Image  # type: ignore[import]  # noqa: PLC0415

        img = Image.open(io.BytesIO(image)).convert("RGB")
        # expand=True ensures the canvas resizes for 90/270 rotations
        rotated = img.rotate(-angle, expand=True)
        buf = io.BytesIO()
        rotated.save(buf, format="PNG")
        return buf.getvalue()
