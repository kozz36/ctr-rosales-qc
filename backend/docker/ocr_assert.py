"""Build-time RapidOCR assertion script (runtime stage).

Second gate after builder warm-up: confirms COPY --from=builder carried all
3 ONNX models into the final image and that cv2 (headless) + ONNX inference
work correctly in the slim runtime environment.

CRITICAL-B guard: cv2 is provided by opencv-python-headless (the uv override
removes the FULL opencv that rapidocr transitively pins). The FULL build links
libGL.so.1/libglib-2.0.so.0 which are absent from python:3.12-slim. Importing
cv2 and running an op here proves headless is active and libGL-free.
"""

import sys
import numpy as np
import cv2  # CRITICAL-B: exercise the native-lib load in the slim runtime
from pathlib import Path
print("cv2 import OK:", cv2.__file__, cv2.__version__)
# Real cv2 op -- fails fast if the native libs (libGL/libglib) are missing,
# which would mean the FULL opencv leaked back past the uv override.
img = np.ones((128, 480, 3), dtype=np.uint8) * 255
cv2.putText(img, "CTR 12345 1/2 KG  REGISTRO 232",
            (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
cv2.putText(img, "ACERO A615 G60  4200 kg  OBRA",
            (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
_gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
print("cv2 cvtColor OK:", _gray.shape)

import rapidocr
from rapidocr import RapidOCR, OCRVersion, ModelType

# Disk-existence guard -- build FAILS here if builder warm-up missed any model.
# This is the second gate: confirms COPY --from=builder carried all 3 models.
models_dir = Path(rapidocr.__file__).parent / "models"
required_models = [
    "ch_PP-OCRv5_det_server.onnx",
    "ch_PP-OCRv5_rec_server.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
]
missing = []
for name in required_models:
    path = models_dir / name
    if not path.exists() or path.stat().st_size < 1024:
        missing.append(f"{name} (exists: {path.exists()})")
    else:
        print(f"  MODEL OK  {name}: {path.stat().st_size:,} bytes")
if missing:
    print("RUNTIME ASSERTION FAILED -- ONNX models not bundled (air-gap violated):", file=sys.stderr)
    for m in missing:
        print(f"  MISSING: {m}", file=sys.stderr)
    sys.exit(1)

# Construct engine offline to confirm models are loadable from the venv cache.
# No network allowed here (builder warm-up must have bundled everything).
engine = RapidOCR(params={
    "Det.ocr_version": OCRVersion.PPOCRV5,
    "Det.model_type": ModelType.SERVER,
    "Rec.ocr_version": OCRVersion.PPOCRV5,
    "Rec.model_type": ModelType.SERVER,
})
# Run ONE real inference using the same synthetic text image -- exercises the full
# cv2 + ONNX path including Cls + Rec.  If any model is absent or a native-lib
# gap exists (FULL opencv leaked back), this fails at BUILD time, not in prod.
result = engine(img)
# result is RapidOCROutput(boxes, txts, scores, ...) -- boxes is np.ndarray or None
n_boxes = len(result.boxes) if result.boxes is not None else 0
print(f"rapidocr + PP-OCRv5-server REAL inference OK ({n_boxes} boxes) -- CONT-S03b PASS (cv2 native libs present, all 3 models bundled)")
