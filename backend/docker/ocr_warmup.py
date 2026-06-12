"""Build-time RapidOCR warm-up script (builder stage).

Runs inference on a synthetic text image so ALL THREE ONNX models
(det_server ~84 MB, rec_server ~81 MB, cls_mobile ~0.6 MB) are downloaded
into venv site-packages/rapidocr/models/ while the network is available.

WHY inference is required (not just construction):
  RapidOCR lazy-loads Cls + Rec only when Det detects text boxes.
  Constructing the engine alone never triggers Cls/Rec downloads.
  Random noise -> Det finds zero boxes -> Cls/Rec never load -> NOT downloaded.
  Synthetic text via cv2.putText -> Det finds boxes -> Cls + Rec lazy-load ->
  all three models downloaded while network is available.

NOTE on cls model naming:
  Cls stays PP-OCRv4/mobile even when Det/Rec are PP-OCRv5/server (default
  config.yaml behaviour) -> the cls file is ch_ppocr_mobile_v2.0_cls_mobile.onnx,
  NOT the package-bundled ch_ppocr_mobile_v2.0_cls_infer.onnx (different name!).
  The disk-existence guard below is the proof that all three are present.
"""

import sys
import numpy as np
import cv2
from rapidocr import RapidOCR, OCRVersion, ModelType
from pathlib import Path
import rapidocr as _rapidocr_pkg

print("RapidOCR warm-up: constructing PP-OCRv5-server engine...")
engine = RapidOCR(params={
    "Det.ocr_version": OCRVersion.PPOCRV5,
    "Det.model_type": ModelType.SERVER,
    "Rec.ocr_version": OCRVersion.PPOCRV5,
    "Rec.model_type": ModelType.SERVER,
})

# Synthetic text image: white background with legible printed text.
# cv2.putText renders real strokes -> Det detects boxes -> Cls + Rec lazy-load.
img = np.ones((128, 480, 3), dtype=np.uint8) * 255
cv2.putText(img, "CTR 12345 1/2 KG  REGISTRO 232",
            (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
cv2.putText(img, "ACERO A615 G60  4200 kg  OBRA",
            (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
print("RapidOCR warm-up: running inference on synthetic text image...")
result = engine(img)
# result is RapidOCROutput(boxes, txts, scores, ...) — boxes is np.ndarray or None
n_boxes = len(result.boxes) if result.boxes is not None else 0
print("RapidOCR warm-up: inference complete, result boxes:", n_boxes)

# Disk-existence guard -- build FAILS if any model was not downloaded.
# This is the air-gap guarantee: every model MUST be physically on disk;
# the COPY --from=builder in the runtime stage carries them into the final image.
models_dir = Path(_rapidocr_pkg.__file__).parent / "models"
required_models = [
    "ch_PP-OCRv5_det_server.onnx",            # Det PP-OCRv5 server ~84 MB
    "ch_PP-OCRv5_rec_server.onnx",            # Rec PP-OCRv5 server ~81 MB
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",   # Cls PP-OCRv4 mobile (default cls)
]
missing = []
for name in required_models:
    path = models_dir / name
    if not path.exists() or path.stat().st_size < 1024:
        missing.append(f"{name} (path: {path}, exists: {path.exists()})")
    else:
        print(f"  OK  {name}: {path.stat().st_size:,} bytes")

if missing:
    print("BUILDER WARM-UP FAILED -- missing ONNX models (air-gap guarantee violated):", file=sys.stderr)
    for m in missing:
        print(f"  MISSING: {m}", file=sys.stderr)
    print("Fix: ensure the synthetic text image triggers Det->Cls+Rec lazy-load.", file=sys.stderr)
    sys.exit(1)

print("RapidOCR warm-up: all 3 ONNX models present on disk -- air-gap ready.")
