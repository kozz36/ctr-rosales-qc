"""Unit tests for RapidOCRAdapter (EXT-028 + EXT-030).

All tests inject a fake engine via the `_engine` seam — NO real rapidocr,
NO onnxruntime, NO Pillow installed required.  The suite MUST run cleanly
without any of those packages.

Covers:
- extract_declared always returns []  (S028a)
- Heavy imports NOT triggered at adapter init  (S028b)
- Engine failure → graceful degradation, returns []  (EXT-028 / graceful)
- Confidence below 0.85 → requires_review=True  (EXT-004 confidence gate)
- Default -90° rotation is applied first  (EXT-030 / S030a)
- Retry triggered when -90° yields 0 valid rows  (S030b)
- Rotation with max valid rows wins  (S030c)
"""

from __future__ import annotations

import sys
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Engine mock builders
# ---------------------------------------------------------------------------


def _make_engine(boxes, txts, scores):
    """Build a fake RapidOCR engine whose __call__ returns a mock result.

    The RapidOCR 3.8.x API: ``engine(img_array)`` returns a result object
    whose ``.boxes`` is a list of 4-point polygon arrays and ``.txts`` /
    ``.scores`` are parallel lists.

    ``boxes`` entries are 4-point polygons as nested lists/tuples:
        [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]  (clockwise corners)
    """
    result = MagicMock()
    result.boxes = boxes
    result.txts = txts
    result.scores = scores

    engine = MagicMock()
    engine.return_value = result
    return engine


def _make_material_engine(
    desc: str = "BARRA CORRUGADA 3/8",
    qty: str = "0.136",
    unit: str = "TN",
    conf: float = 0.92,
    y_base: float = 150.0,
) -> object:
    """Build an engine returning one valid material row (desc, qty, unit).

    Polygon centroids are placed so desc=cx100, qty=cx250, unit=cx320 — all
    on the same row band (same cy).
    """
    def _box(cx: float, cy: float) -> list[list[float]]:
        # 4-point polygon that encloses a 10×10 region around (cx, cy).
        return [
            [cx - 5, cy - 5],
            [cx + 5, cy - 5],
            [cx + 5, cy + 5],
            [cx - 5, cy + 5],
        ]

    boxes = [_box(100, y_base), _box(250, y_base), _box(320, y_base)]
    txts = [desc, qty, unit]
    scores = [conf, conf, conf]
    return _make_engine(boxes, txts, scores)


def _make_empty_engine() -> object:
    """Build an engine returning zero boxes (no recognisable content)."""
    return _make_engine([], [], [])


class _NdarrayLikeBoxes:
    """Faithful stub of RapidOCROutput.boxes (np.ndarray of shape (N,4,2)).

    Reproduces the exact runtime contract of a real ``numpy.ndarray`` with
    more than one element WITHOUT requiring numpy installed:

    - ``__bool__`` RAISES ``ValueError`` (numpy's "truth value of an array with
      more than one element is ambiguous") — this is what makes the legacy
      ``not result.boxes`` guard crash on every real multi-box page.
    - ``__len__`` / ``__iter__`` work over the N 4-point polygon rows, so a
      None/len()==0 guard and ``zip`` iteration both behave like the real array.
    """

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def __bool__(self) -> bool:  # pragma: no cover - exercised via guard
        raise ValueError(
            "The truth value of an array with more than one element is ambiguous. "
            "Use a.any() or a.all()"
        )

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._rows)


def _make_ndarray_like_engine(
    desc: str = "BARRA CORRUGADA 3/8",
    qty: str = "0.136",
    unit: str = "TN",
    conf: float = 0.92,
    y_base: float = 150.0,
) -> object:
    """Engine whose result.boxes mimics a real numpy ndarray (truthiness raises).

    txts/scores are TUPLES (the real RapidOCROutput types), not lists.
    """
    def _box(cx: float, cy: float) -> list[list[float]]:
        return [
            [cx - 5, cy - 5],
            [cx + 5, cy - 5],
            [cx + 5, cy + 5],
            [cx - 5, cy + 5],
        ]

    rows = [_box(100, y_base), _box(250, y_base), _box(320, y_base)]
    result = MagicMock()
    result.boxes = _NdarrayLikeBoxes(rows)
    result.txts = (desc, qty, unit)
    result.scores = (conf, conf, conf)

    engine = MagicMock()
    engine.return_value = result
    return engine


def _dummy_rotate(image: bytes, angle: int) -> object:
    """No-op rotate_fn injected in tests — returns a sentinel object.

    The real PIL+numpy rotate path is bypassed so tests can pass synthetic
    (non-parseable) image bytes.  The engine mock ignores the array value.
    """
    return object()  # sentinel — engine mock ignores this value


# ---------------------------------------------------------------------------
# Tests — basic adapter behaviour (S028a, S028b, graceful degradation)
# ---------------------------------------------------------------------------


class TestRapidOCRAdapterBasic:
    def test_extract_declared_returns_empty_list(self) -> None:
        """S028a: extract_declared is a no-op — always returns []."""
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        adapter = RapidOCRAdapter(_engine=_make_empty_engine(), _rotate_fn=_dummy_rotate)
        result = adapter.extract_declared("any declared text")
        assert result == []

    def test_lazy_import_not_triggered_at_init(self) -> None:
        """S028b: instantiating RapidOCRAdapter MUST NOT import rapidocr/onnxruntime/PIL/numpy.

        The suite runs without those packages installed.  If any of them are
        imported at module top-level or in __init__, this test will fail with
        a ModuleNotFoundError (or catch an import that was already present
        in sys.modules from a previous test — either way the invariant holds).
        """
        heavy = {"rapidocr", "onnxruntime", "PIL", "numpy"}

        # Capture sys.modules snapshot BEFORE import of the adapter module.
        # We use importlib to force a clean check even if the module was cached.
        before = set(sys.modules.keys())

        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter  # noqa: PLC0415

        # Instantiate with injected engine — no real engine construction.
        adapter = RapidOCRAdapter(_engine=_make_empty_engine())
        _ = adapter  # suppress unused-variable warning

        # New keys after import + init must not include any heavy dep.
        after = set(sys.modules.keys())
        newly_imported = after - before
        leaked = heavy & {k.split(".")[0] for k in newly_imported}
        assert not leaked, (
            f"Heavy SDK(s) imported at adapter init: {leaked}. "
            "All of rapidocr/onnxruntime/PIL/numpy MUST be lazy (inside methods)."
        )

    def test_engine_failure_returns_empty_not_raises(self) -> None:
        """Graceful degradation: if the engine raises, extract_printed_table returns []."""
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        bad_engine = MagicMock()
        bad_engine.side_effect = RuntimeError("simulated engine crash")

        adapter = RapidOCRAdapter(_engine=bad_engine, _rotate_fn=_dummy_rotate)
        result = adapter.extract_printed_table(b"\x89PNG")
        assert result == [], "Engine failure must degrade gracefully to empty list"


# ---------------------------------------------------------------------------
# Tests — confidence gate (EXT-004 retained)
# ---------------------------------------------------------------------------


class TestRapidOCRAdapterConfidence:
    def test_confidence_below_threshold_sets_requires_review(self) -> None:
        """Low-confidence OCR box → requires_review=True (EXT-004 confidence gate)."""
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        # Score 0.75 is below the 0.85 threshold.
        engine = _make_material_engine(conf=0.75)
        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")
        assert lines, "Expected at least one line from the mock engine"
        assert all(
            line.requires_review for line in lines
        ), "All lines with conf < 0.85 must have requires_review=True"

    def test_confidence_above_threshold_no_requires_review(self) -> None:
        """High-confidence OCR box → requires_review=False when geometry is clean."""
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        engine = _make_material_engine(conf=0.92)
        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")
        assert lines, "Expected at least one line from the mock engine"
        # All lines should be high-confidence (requires_review=False), because the
        # mock geometry is clean (DESC | QTY | UNIT, same row band, column order correct).
        assert all(
            not line.requires_review for line in lines
        ), "High-confidence, geometrically-clean line must not require review"


# ---------------------------------------------------------------------------
# Tests — orientation retry logic (EXT-030, S030a-c)
# ---------------------------------------------------------------------------


class TestRapidOCRAdapterOrientation:
    """Test the self-scoring orientation retry loop in RapidOCRAdapter.

    The adapter logic:
      1. Default: rotate image -90° first.
      2. Run parse_box_rows on the result.
      3. If 0 valid rows → try all 4 rotations {0, 90, 180, 270}.
      4. Pick the rotation with the MOST valid rows.

    Engine is injected, images are synthetic (we only check call counts and
    row-count selection, not pixel content).
    """

    def test_default_minus90_applied_first(self) -> None:
        """S030a: the -90° rotation is the FIRST rotation tried.

        We verify this by injecting an engine that returns a valid row — if
        -90° is tried first and yields rows, no retry occurs.
        """
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        engine = _make_material_engine()
        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")

        # Engine must have been called exactly once (no retry needed).
        assert engine.call_count == 1, (
            f"Expected 1 engine call (default -90° succeeded), got {engine.call_count}"
        )
        assert len(lines) >= 1, "Default -90° should yield at least 1 row from mock"

    def test_retry_triggered_on_zero_valid_rows(self) -> None:
        """S030b: when -90° yields 0 rows, the adapter retries the other 3 rotations.

        Total engine calls = 1 (default -90°) + 4 (retry {0, 90, 180, 270}) = 5.
        """
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        # Engine always returns empty boxes — every rotation yields 0 rows.
        engine = _make_empty_engine()
        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")

        assert lines == [], "No rows from any rotation → empty list"
        # Must have tried more than 1 rotation (retry was triggered).
        assert engine.call_count > 1, (
            f"Expected >1 engine call (retry triggered), got {engine.call_count}"
        )

    def test_max_valid_rows_wins_on_retry(self) -> None:
        """S030c: after retry, the rotation with the MOST valid rows is selected.

        We inject an engine that returns 0 rows on the first call (-90°) and a
        real material row on one of the subsequent rotation attempts.  The adapter
        must return the rows from the winning rotation.
        """
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        # First call (default -90°): empty.
        # Second call onward: one valid row.
        call_count = {"n": 0}

        def _side_effect(img_array):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Default rotation: no boxes.
                result = MagicMock()
                result.boxes = []
                result.txts = []
                result.scores = []
                return result
            else:
                # Retry rotations: return a single valid material row.
                def _box(cx: float, cy: float) -> list[list[float]]:
                    return [
                        [cx - 5, cy - 5],
                        [cx + 5, cy - 5],
                        [cx + 5, cy + 5],
                        [cx - 5, cy + 5],
                    ]

                result = MagicMock()
                result.boxes = [
                    _box(100, 150), _box(250, 150), _box(320, 150)
                ]
                result.txts = ["BARRA CORRUGADA 3/8", "0.136", "TN"]
                result.scores = [0.92, 0.92, 0.92]
                return result

        engine = MagicMock(side_effect=_side_effect)
        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")

        assert len(lines) >= 1, (
            "Retry must pick the rotation with the most rows (at least 1)"
        )
        assert engine.call_count > 1, "Retry must have been triggered (>1 call)"


# ---------------------------------------------------------------------------
# Tests — REAL RapidOCROutput shape (C1: ndarray boxes, tuple txts/scores)
# ---------------------------------------------------------------------------


class TestRapidOCRAdapterRealOutputShape:
    """C1 regression: the real ``RapidOCROutput.boxes`` is an ``np.ndarray``
    of shape ``(N,4,2)`` (NOT a Python list), and ``txts``/``scores`` are
    tuples.  ``not result.boxes`` raises ``ValueError`` on a multi-box ndarray,
    which the blanket ``except`` swallowed → the adapter silently returned []
    on EVERY real guía page (the exact #40 quantity-accuracy failure).
    """

    def test_ndarray_like_boxes_do_not_silently_drop_rows(self) -> None:
        """RED-then-green: ndarray-truthiness boxes must yield real rows, not [].

        The faithful stub's ``__bool__`` raises ValueError exactly like a real
        multi-element ndarray.  Against the legacy ``if not result.boxes`` guard
        this raises (caught by extract_printed_table → []), so the assertion
        ``lines`` is empty → FAIL.  After the explicit None/len() guard +
        numpy-agnostic centroid, the adapter parses the row → PASS.
        """
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        engine = _make_ndarray_like_engine()
        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")

        assert lines, (
            "ndarray-shaped boxes must parse to real MaterialLines, not [] — "
            "the legacy `not result.boxes` guard crashed on ndarray truthiness "
            "and silently dropped every real guía page (C1)."
        )
        assert str(lines[0].cantidad) == "0.136"
        assert lines[0].unidad == "TN"

    def test_numpy_real_ndarray_boxes(self) -> None:
        """Same contract using a REAL numpy ndarray when numpy is importable.

        Guarded by importorskip so the suite still runs with numpy uninstalled
        (the adapter purity contract).  Proves the centroid math and guard work
        on a genuine ``(N,4,2)`` float array, not just the stub.
        """
        np = pytest.importorskip("numpy")
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        def _box(cx: float, cy: float) -> list[list[float]]:
            return [
                [cx - 5, cy - 5],
                [cx + 5, cy - 5],
                [cx + 5, cy + 5],
                [cx - 5, cy + 5],
            ]

        boxes = np.array(
            [_box(100, 150), _box(250, 150), _box(320, 150)], dtype="float64"
        )
        assert boxes.shape == (3, 4, 2)
        result = MagicMock()
        result.boxes = boxes
        result.txts = ("BARRA CORRUGADA 3/8", "0.136", "TN")
        result.scores = (0.92, 0.92, 0.92)
        engine = MagicMock(return_value=result)

        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")

        assert lines, "Real (N,4,2) ndarray boxes must parse to MaterialLines"
        assert str(lines[0].cantidad) == "0.136"
        assert lines[0].unidad == "TN"

    def test_none_txts_with_present_boxes_is_defensive(self) -> None:
        """Defensive: boxes present but txts/scores None → graceful [], no crash."""
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        rows = [[[95.0, 145.0], [105.0, 145.0], [105.0, 155.0], [95.0, 155.0]]]
        result = MagicMock()
        result.boxes = _NdarrayLikeBoxes(rows)
        result.txts = None
        result.scores = None
        engine = MagicMock(return_value=result)

        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")
        assert lines == [], "None txts/scores must degrade to [] without raising"


# ---------------------------------------------------------------------------
# Tests — orientation oracle scores CONFIDENT rows, not raw count (W1)
# ---------------------------------------------------------------------------


def _box(cx: float, cy: float) -> list[list[float]]:
    return [
        [cx - 5, cy - 5],
        [cx + 5, cy - 5],
        [cx + 5, cy + 5],
        [cx - 5, cy + 5],
    ]


class TestRapidOCRAdapterOrientationOracleConfidence:
    """W1: the orientation oracle must score by CONFIDENT rows (requires_review
    is False), NOT raw parsed-row count.

    A garbage rotation that produces parseable-but-flagged rows
    (requires_review=True) must score BELOW a rotation producing confident,
    in-profile rows — otherwise the wrong orientation can tie/win and surface
    review-flagged quantities as the chosen page read.
    """

    def test_confident_rotation_beats_more_flagged_rows(self) -> None:
        """A rotation with FEWER confident rows must still beat a rotation with
        MORE rows that are ALL requires_review=True.

        Call 1 = default -90°: empty (forces the retry loop over {0,90,180,270}).
        Then within the retry loop we feed, in _RETRY_ANGLES order:
          - angle 0   -> TWO relaxed (unit-left-of-qty) rows => 2 flagged rows,
                         0 confident.
          - angle 90  -> ONE clean DESC|QTY|UNIT row => 1 confident row.
          - angle 180 -> empty.
          - angle 270 -> empty.

        Raw-count scoring would pick angle 0 (2 rows) and return flagged rows.
        Confident-count scoring must pick angle 90 (1 confident row) and return
        exactly that single confident, non-review row.
        """
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter

        # Two relaxed rows: unit is LEFT of qty (cx_unit < cx_qty) -> relaxed
        # fallback path in parse_box_rows -> requires_review=True, but parseable.
        flagged_boxes = [
            _box(100, 150), _box(320, 150), _box(250, 150),   # row 1: desc, qty(right), unit(left of qty)
            _box(100, 250), _box(320, 250), _box(250, 250),   # row 2: same shape
        ]
        flagged_txts = (
            "BARRA CORRUGADA 3/8", "0.136", "TN",
            "BARRA CORRUGADA 1/2", "0.250", "TN",
        )
        flagged_scores = (0.92,) * 6

        # One clean confident row: DESC | QTY | UNIT, cx ascending -> confident.
        confident_boxes = [_box(100, 150), _box(250, 150), _box(320, 150)]
        confident_txts = ("BARRA CORRUGADA 3/8", "0.136", "TN")
        confident_scores = (0.92, 0.92, 0.92)

        empty = ([], (), ())

        # Sequence: call 1 = default -90° (empty), then the four retry angles.
        sequence = [
            empty,                                                  # -90° default
            (flagged_boxes, flagged_txts, flagged_scores),          # angle 0
            (confident_boxes, confident_txts, confident_scores),    # angle 90
            empty,                                                  # angle 180
            empty,                                                  # angle 270
        ]
        idx = {"n": 0}

        def _side_effect(_img):  # type: ignore[no-untyped-def]
            boxes, txts, scores = sequence[idx["n"]]
            idx["n"] += 1
            result = MagicMock()
            result.boxes = boxes
            result.txts = txts
            result.scores = scores
            return result

        engine = MagicMock(side_effect=_side_effect)
        adapter = RapidOCRAdapter(_engine=engine, _rotate_fn=_dummy_rotate)
        lines = adapter.extract_printed_table(b"\x89PNG")

        # Confident-count scoring picks angle 90 (1 confident row), NOT angle 0
        # (2 flagged rows). The winning page is the all-confident one.
        assert len(lines) == 1, (
            f"Expected the confident rotation (1 confident row) to win, got "
            f"{len(lines)} rows: {[(l.description_raw, l.requires_review) for l in lines]}"
        )
        assert lines[0].requires_review is False, (
            "The selected rotation must be the all-confident one, not the "
            "rotation with more review-flagged rows (W1)."
        )
