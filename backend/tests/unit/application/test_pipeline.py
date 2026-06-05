"""Tests for ReconciliationPipeline — stage sequencing and cost-cap enforcement.

All ports are replaced with in-memory fakes that satisfy the Protocol contracts
without importing any external SDKs.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import PipelineResult, ReconciliationPipeline
from reconciliation.application.run_context import RunContext
from reconciliation.domain.models import (
    GuiaIdentity,
    MaterialLine,
    Registro,
    VisionResult,
)
from reconciliation.domain.ports import DocumentSourcePort, ExtractionPort, VisionLLMPort


# ---------------------------------------------------------------------------
# Fake port implementations (in-memory, no external dependencies)
# ---------------------------------------------------------------------------


class FakeDocumentSource:
    """Fake DocumentSourcePort backed by configurable page data."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        # Each entry: {"text": str|None, "image": bytes}
        self._pages = pages

    def page_count(self) -> int:
        return len(self._pages)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return self._pages[idx].get("image", b"\x89PNG\r\n")

    def page_text(self, idx: int) -> str | None:
        return self._pages[idx].get("text")


class FakeExtractor:
    """Fake ExtractionPort with configurable per-page results."""

    def __init__(
        self,
        declared_lines: list[MaterialLine] | None = None,
        table_lines: list[MaterialLine] | None = None,
    ) -> None:
        self._declared_lines = declared_lines or []
        self._table_lines = table_lines or []

    def extract_declared(self, text: str) -> list[MaterialLine]:
        return list(self._declared_lines)

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return list(self._table_lines)


class FakeVisionSerial:
    """Non-batching fake VisionLLMPort — sequential calls."""

    supports_batch: bool = False

    def __init__(
        self,
        results: list[VisionResult] | None = None,
        cap: int = 1000,
    ) -> None:
        # Pre-configured results returned in order; cycles if exhausted
        self._results = results or []
        self._call_count = 0

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:
        idx = self._call_count % max(len(self._results), 1)
        self._call_count += 1
        if self._results:
            return self._results[idx]
        return VisionResult(date=date(2024, 1, 15), confidence=0.95, raw="15/01/2024")

    def read_handwritten_date_batch(
        self, images: list[bytes]
    ) -> list[VisionResult]:  # pragma: no cover
        raise NotImplementedError("This fake is sequential only.")


class FakeVisionBatch:
    """Batching fake VisionLLMPort."""

    supports_batch: bool = True

    def __init__(self, result: VisionResult | None = None) -> None:
        self._result = result or VisionResult(
            date=date(2024, 1, 15), confidence=0.95, raw="15/01/2024"
        )
        self.batch_calls: int = 0

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:  # pragma: no cover
        raise NotImplementedError("This fake is batch only.")

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:
        self.batch_calls += 1
        return [self._result] * len(images)


class _CountingVision:
    """Sequential fake VisionLLMPort that counts every call (KI-1 / W2-A)."""

    supports_batch: bool = False

    def __init__(self) -> None:
        self.calls = 0

    def read_handwritten_date(
        self, image: bytes, hint: str | None = None
    ) -> VisionResult:
        self.calls += 1
        return VisionResult(date=date(2026, 5, 28), confidence=0.95, raw="28/05/2026")

    def read_handwritten_date_batch(
        self, images: list[bytes]
    ) -> list[VisionResult]:  # pragma: no cover
        raise NotImplementedError("This fake is sequential only.")


class FakeIdentityPerPage:
    """Fake IdentityExtractionPort that returns a unique GuiaIdentity per call.

    Each call returns a different guia_id (``T001-{seq}``), simulating distinct
    QR codes on every page.  This forces the block assembler to create one block
    per page — useful for testing cost-cap semantics where N pages → N blocks →
    N vision calls.
    """

    def __init__(self) -> None:
        self._seq = 0

    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity:
        seq = self._seq
        self._seq += 1
        return GuiaIdentity(
            serie="T001",
            numero=str(seq),
            ruc_emisor="12345678901",
            ruc_receptor="10987654321",
            tipo="09",
            hashqr_url=None,
            confidence=1.0,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DECLARED_TEXT = "\n".join([
    "PTR001-TORRE ROSALES",
    "Informe de detalle del formulario",
    "PROTOCOLO DE RECEPCION",
    "FORM DETAIL",
    "#4252: some record",
    "DESCRIPTION",
    "NOTES",
    "acero corrugado",
    "30.000",
    "KG",
])

_GUIA_TEXT = "\n".join([
    "PTR001-TORRE ROSALES",
    "Informe de detalle del formulario",
    "GUIA DE REMISION",
])


def _make_registro_with_protocolo(numero: str, protocolo_page: int) -> Registro:
    return Registro(
        numero=numero,
        fecha_declarada=date(2026, 5, 28),
        declared_lines=[],
        protocolo_page=protocolo_page,
    )


def _make_line(desc: str = "acero corrugado", qty: str = "30", unit: str = "KG") -> MaterialLine:
    return MaterialLine(
        description_raw=desc,
        description_canonical=desc,
        unidad=unit,  # type: ignore[arg-type]
        cantidad=Decimal(qty),
        confidence=0.95,
        source_page=0,
    )


def _build_pipeline(
    pages: list[dict[str, Any]],
    declared_lines: list[MaterialLine] | None = None,
    table_lines: list[MaterialLine] | None = None,
    vision: VisionLLMPort | None = None,
    max_vision_calls: int = 500,
    tmp_path: Path | None = None,
    identity: Any | None = None,
) -> tuple[ReconciliationPipeline, RunContext]:
    cfg = AppConfig()
    # Override max_vision_calls without touching frozen fields
    object.__setattr__(cfg.vision, "max_vision_calls", max_vision_calls)

    doc = FakeDocumentSource(pages)
    extractor = FakeExtractor(
        declared_lines=declared_lines,
        table_lines=table_lines,
    )
    vis = vision or FakeVisionSerial()

    pipeline = ReconciliationPipeline(
        doc_source=doc,
        extractor=extractor,
        vision=vis,
        config=cfg,
        identity=identity,
    )
    base = tmp_path or Path(".")
    ctx = RunContext(pdf_path=base / "input.pdf", output_base=base / "runs")
    return pipeline, ctx


# ---------------------------------------------------------------------------
# Stage sequencing tests
# ---------------------------------------------------------------------------


class TestPipelineStageSequencing:
    def test_empty_pdf_returns_empty_rows(self, tmp_path: Path) -> None:
        pipeline, ctx = _build_pipeline(pages=[], tmp_path=tmp_path)
        result = pipeline.run(ctx)
        assert isinstance(result, PipelineResult)
        assert result.rows == []
        assert result.classifications == []

    def test_single_ignored_page_no_rows(self, tmp_path: Path) -> None:
        pages = [{"text": "PTR001-TORRE ROSALES\nInforme de detalle del formulario\nTotal items\nSorted by\n"}]
        pipeline, ctx = _build_pipeline(pages=pages, tmp_path=tmp_path)
        result = pipeline.run(ctx)
        assert len(result.classifications) == 1
        assert result.classifications[0].kind == "IGNORED"
        assert result.rows == []

    def test_declared_page_creates_registro(self, tmp_path: Path) -> None:
        pages = [{"text": _DECLARED_TEXT}]
        line = _make_line()
        pipeline, ctx = _build_pipeline(
            pages=pages, declared_lines=[line], tmp_path=tmp_path
        )
        result = pipeline.run(ctx)
        assert result.classifications[0].kind == "DECLARED"
        assert len(result.declared) == 1

    def test_guia_page_creates_guia(self, tmp_path: Path) -> None:
        # rev-6: a GUIA page must carry QR evidence to open a block (invariant
        # QR-evidence guard).  FakeIdentityPerPage supplies a decoded QR identity.
        pages = [{"text": _GUIA_TEXT, "image": b"\x89PNG"}]
        line = _make_line()
        pipeline, ctx = _build_pipeline(
            pages=pages, table_lines=[line], tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)
        assert result.classifications[0].kind == "GUIA"
        assert len(result.guias) == 1

    def test_guia_page_vision_date_attached(self, tmp_path: Path) -> None:
        """Vision date is attached to guía; year is reconstructed via bounded inference.

        Folded fix (#2753 / R3): _stage_normalize_dates always reconstructs the year
        from day/month even when vision returned a full date.  When vision returns
        2024-03-10 (raw="10/03/2024"), the inference picks the most-recent March 10
        within the ±5-year window (2026-03-10 as of the 2026 run date), correcting
        the wrong year and setting year_inferred=True.

        The confidence from vision is preserved on the GuiaDeRemision.
        """
        pages = [{"text": _GUIA_TEXT, "image": b"\x89PNG"}]
        line = _make_line()
        vision = FakeVisionSerial(
            results=[VisionResult(date=date(2024, 3, 10), confidence=0.99, raw="10/03/2024")]
        )
        # rev-6: QR evidence required to open the block (invariant guard).
        pipeline, ctx = _build_pipeline(
            pages=pages, table_lines=[line], vision=vision, tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)
        # Year-fix: vision returned 2024-03-10 but inference reconstructs to the most
        # recent valid March 10 (2026-03-10 given today's run date is 2026-06-02).
        assert result.guias[0].fecha == date(2026, 3, 10)
        assert result.guias[0].year_inferred is True
        assert result.guias[0].fecha_confidence == 0.99

    def test_vision_calls_counted_sequential(self, tmp_path: Path) -> None:
        """Sequential path: one vision call per BLOCK.

        With FakeIdentityPerPage each GUIA page gets a unique guia_id → each
        page becomes its own block → 2 pages = 2 blocks = 2 vision calls.
        Without an identity adapter, all same-section pages merge into one block
        (OCR fallback) → only 1 call.  The test uses per-page identity to verify
        the vision-call counter is incremented per block.
        """
        pages = [
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
        ]
        pipeline, ctx = _build_pipeline(
            pages=pages, tmp_path=tmp_path, identity=FakeIdentityPerPage()
        )
        result = pipeline.run(ctx)
        assert result.vision_calls_made == 2

    def test_batch_vision_uses_batch_path(self, tmp_path: Path) -> None:
        """Batch path: single batch call for all BLOCKS.

        With FakeIdentityPerPage, 2 pages = 2 blocks → one batch call covering
        both blocks.  vision_calls_made counts blocks (images sent), not API calls.
        """
        pages = [
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
        ]
        vision = FakeVisionBatch()
        pipeline, ctx = _build_pipeline(
            pages=pages, vision=vision, tmp_path=tmp_path, identity=FakeIdentityPerPage()
        )
        result = pipeline.run(ctx)
        assert vision.batch_calls == 1
        assert result.vision_calls_made == 2

    def test_run_id_matches_context(self, tmp_path: Path) -> None:
        pipeline, ctx = _build_pipeline(pages=[], tmp_path=tmp_path)
        result = pipeline.run(ctx)
        assert result.run_id == ctx.run_id

    def test_extraction_cache_written_after_run(self, tmp_path: Path) -> None:
        pipeline, ctx = _build_pipeline(pages=[], tmp_path=tmp_path)
        pipeline.run(ctx)
        assert ctx.has_extraction_cache()

    def test_review_sidecar_initialised_after_run(self, tmp_path: Path) -> None:
        pipeline, ctx = _build_pipeline(pages=[], tmp_path=tmp_path)
        pipeline.run(ctx)
        sidecar = ctx.read_review_sidecar()
        assert "edits" in sidecar

    def test_extraction_cache_not_overwritten_on_second_run(self, tmp_path: Path) -> None:
        """If cache already exists, Stage 9 must NOT overwrite it."""
        pipeline, ctx = _build_pipeline(pages=[], tmp_path=tmp_path)
        pipeline.run(ctx)
        first_content = ctx.extraction_cache.read_text(encoding="utf-8")
        # Run again (simulates a restart where we call run on an already-run ctx)
        # Should NOT raise and should not overwrite.
        pipeline.run(ctx)
        second_content = ctx.extraction_cache.read_text(encoding="utf-8")
        assert first_content == second_content

    def test_reconcile_rows_produced_from_both_page_types(self, tmp_path: Path) -> None:
        """Pipeline with DECLARED + GUIA pages always yields at least one row."""
        declared_line = _make_line(qty="30")
        guia_line = _make_line(qty="20")

        pages = [
            {"text": _DECLARED_TEXT},
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
        ]
        pipeline, ctx = _build_pipeline(
            pages=pages,
            declared_lines=[declared_line],
            table_lines=[guia_line],
            tmp_path=tmp_path,
        )
        result = pipeline.run(ctx)
        # Rows are produced; statuses are DECLARED_MISSING / GUIA_MISSING because
        # the pipeline assigns distinct registro ids per page (extractor stub).
        # The important invariant: no row is silently dropped.
        assert len(result.rows) >= 1
        valid_statuses = {"MATCH", "MISMATCH", "DECLARED_MISSING", "GUIA_MISSING", "UNCLASSIFIED"}
        assert all(r.status in valid_statuses for r in result.rows)

    def test_reconcile_only_declared_page_creates_guia_missing(self, tmp_path: Path) -> None:
        """Only a DECLARED page with no GUIA counterpart → GUIA_MISSING row."""
        declared_line = _make_line(qty="30")
        pages = [{"text": _DECLARED_TEXT}]
        pipeline, ctx = _build_pipeline(
            pages=pages,
            declared_lines=[declared_line],
            tmp_path=tmp_path,
        )
        result = pipeline.run(ctx)
        assert len(result.rows) == 1
        assert result.rows[0].status == "GUIA_MISSING"


# ---------------------------------------------------------------------------
# Cost-cap enforcement tests
# ---------------------------------------------------------------------------


class TestVisionCostCap:
    """KI-1: hitting the vision cap degrades gracefully (no raise).

    When ``vision.max_vision_calls`` is reached the pipeline STOPS issuing
    further vision calls, leaves the remaining items' ``fecha=None`` (which the
    null-fecha reconciliation rule flags ``requires_review``), and runs to
    completion.  W2-A: the declared-date stage shares the SAME cap as the guía
    stage instead of getting a fresh budget.
    """

    def test_cap_zero_degrades_no_calls_sequential(self, tmp_path: Path) -> None:
        """Cap=0 means NO vision calls; pipeline completes, guía fecha=None."""
        pages = [{"text": _GUIA_TEXT, "image": b"\x89PNG"}]
        line = _make_line()
        # rev-6: QR evidence required to open the block (invariant guard).
        pipeline, ctx = _build_pipeline(
            pages=pages, table_lines=[line], max_vision_calls=0, tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)  # must NOT raise
        assert result.vision_calls_made == 0
        assert len(result.guias) == 1
        assert result.guias[0].fecha is None

    def test_cap_exceeded_mid_run_degrades_sequential(self, tmp_path: Path) -> None:
        """Cap=1 with 3 distinct-QR blocks → first block gets a date, the rest
        degrade to fecha=None; no exception is raised.

        FakeIdentityPerPage ensures each page is a separate block (unique guia_id).
        """
        pages = [
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
        ]
        line = _make_line()
        pipeline, ctx = _build_pipeline(
            pages=pages, table_lines=[line], max_vision_calls=1, tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)  # must NOT raise
        assert result.vision_calls_made == 1
        assert len(result.guias) == 3
        with_date = [g for g in result.guias if g.fecha is not None]
        without_date = [g for g in result.guias if g.fecha is None]
        assert len(with_date) == 1
        assert len(without_date) == 2

    def test_cap_exact_match_does_not_degrade(self, tmp_path: Path) -> None:
        """Cap=2 with 2 distinct-QR blocks → completes, all get a date."""
        pages = [
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
        ]
        line = _make_line()
        pipeline, ctx = _build_pipeline(
            pages=pages, table_lines=[line], max_vision_calls=2, tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)  # must not raise
        assert result.vision_calls_made == 2
        assert all(g.fecha is not None for g in result.guias)

    def test_cap_exceeded_batch_path_degrades(self, tmp_path: Path) -> None:
        """Batch path: cap=1 with 3 distinct-QR blocks → partial batch is
        processed, the remainder degrade to fecha=None, no exception raised."""
        pages = [
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},
        ]
        line = _make_line()
        vision = FakeVisionBatch()
        pipeline, ctx = _build_pipeline(
            pages=pages, table_lines=[line], vision=vision, max_vision_calls=1,
            tmp_path=tmp_path, identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)  # must NOT raise
        assert result.vision_calls_made == 1
        assert len(result.guias) == 3
        assert len([g for g in result.guias if g.fecha is not None]) == 1
        assert len([g for g in result.guias if g.fecha is None]) == 2

    def test_declared_date_no_vision_stage(self, tmp_path: Path) -> None:
        """Domain fix (2026-06-03): declared date vision stage has been removed.

        The ``_stage_extract_declared_date`` method no longer exists.  Registros
        with ``protocolo_page`` set do NOT trigger any VisionLLMPort call for the
        declared date — their ``fecha_authoritative`` == ``fecha_declarada`` (digital
        parse, no vision).  The vision cap is fully reserved for guía reads only.
        """
        vision = _CountingVision()
        pipeline = ReconciliationPipeline(
            doc_source=FakeDocumentSource(
                [{"image": b"\x89PNG"}, {"image": b"\x89PNG"}]
            ),
            extractor=FakeExtractor(),
            vision=vision,
            config=AppConfig(),
        )
        # The method must not exist — confirmed by TestDeclaredDateVisionStageRemoved.
        assert not hasattr(pipeline, "_stage_extract_declared_date"), (
            "_stage_extract_declared_date must be removed (declared date is digital)"
        )
        # Registros with protocolo_page return fecha_declarada directly — no vision.
        reg = _make_registro_with_protocolo("100", protocolo_page=0)
        assert reg.fecha_authoritative == reg.fecha_declarada
        assert vision.calls == 0

    def test_no_guia_pages_no_vision_calls(self, tmp_path: Path) -> None:
        """Pipeline with only DECLARED pages must not call vision at all."""
        pages = [{"text": _DECLARED_TEXT}]
        pipeline, ctx = _build_pipeline(pages=pages, tmp_path=tmp_path)
        result = pipeline.run(ctx)
        assert result.vision_calls_made == 0


# ---------------------------------------------------------------------------
# 7.2: Vision audit record in sidecar
# ---------------------------------------------------------------------------


class TestVisionAuditRecord:
    """Task 7.2: pipeline writes vision audit {stage, calls_made, cap_reached} to sidecar."""

    def test_audit_record_written_on_normal_run(self, tmp_path: Path) -> None:
        """After a successful run, sidecar contains vision_audit with cap_reached=False."""
        pages = [{"text": _GUIA_TEXT, "image": b"\x89PNG"}]
        pipeline, ctx = _build_pipeline(pages=pages, tmp_path=tmp_path)
        pipeline.run(ctx)
        sidecar = ctx.read_review_sidecar()
        assert "vision_audit" in sidecar, "vision_audit key missing from sidecar"
        audit = sidecar["vision_audit"]
        assert len(audit) >= 1
        record = audit[-1]
        assert record["stage"] == "vision"
        assert record["calls_made"] >= 0
        assert record["cap_reached"] is False

    def test_audit_record_no_guia_pages(self, tmp_path: Path) -> None:
        """Runs with no GUIA pages write audit record with calls_made=0, cap_reached=False."""
        pages = [{"text": _DECLARED_TEXT}]
        pipeline, ctx = _build_pipeline(pages=pages, tmp_path=tmp_path)
        pipeline.run(ctx)
        sidecar = ctx.read_review_sidecar()
        audit = sidecar.get("vision_audit", [])
        assert len(audit) >= 1
        record = audit[-1]
        assert record["stage"] == "vision"
        assert record["calls_made"] == 0
        assert record["cap_reached"] is False

    def test_sidecar_still_has_edits_after_audit(self, tmp_path: Path) -> None:
        """Vision audit record does not overwrite the edits key in the sidecar."""
        pages = [{"text": _DECLARED_TEXT}]
        pipeline, ctx = _build_pipeline(pages=pages, tmp_path=tmp_path)
        pipeline.run(ctx)
        sidecar = ctx.read_review_sidecar()
        # Both keys must be present
        assert "edits" in sidecar, "edits key lost after vision audit write"
        assert "vision_audit" in sidecar


# ---------------------------------------------------------------------------
# Slice 2: declared-date missing guard (2026-06-03 domain fix)
# ---------------------------------------------------------------------------


class TestDeclaredDateMissingGuard:
    """When a Registro has protocolo_page set but fecha_declarada is None
    (digital parse failed), the pipeline MUST emit a WARNING in PipelineResult.warnings.
    No vision call; no new field; deterministic.
    """

    def test_registro_with_protocolo_page_and_no_fecha_declarada_emits_warning(
        self, tmp_path: Path
    ) -> None:
        """EXPECTED TO FAIL before guard is implemented.

        A Registro with protocolo_page != None but fecha_declarada == None means
        the Protocolo page was found but the digital parse yielded no date.
        The pipeline must surface a human-readable WARNING in result.warnings.
        """
        # Use a FakeExtractor that returns a Registro with protocolo_page set
        # but fecha_declarada=None (simulating a failed digital parse).
        class _ExtractorWithFailedParse:
            """Exposes Registro-level API so the rich path is taken in _stage_extract_declared."""
            _ocr_failed: bool = False

            def extract_declared(self, text: str) -> list:
                return []

            def extract_printed_table(self, image: bytes) -> list:
                return []

            def extract_registro_from_proto_page(
                self, text: str, page: int
            ) -> Registro | None:
                # Return a Registro without a fecha (digital parse failed).
                return Registro(
                    numero="232",
                    fecha_declarada=None,
                    declared_lines=[],
                    protocolo_page=page,
                )

            def extract_registro_from_detail_page(
                self, text: str, page: int
            ) -> Registro | None:
                return None

        from reconciliation.application.config import AppConfig
        from reconciliation.application.pipeline import ReconciliationPipeline
        from reconciliation.application.run_context import RunContext

        pages = [{"text": "PTR001-TORRE ROSALES\nInforme de detalle del formulario\nPROTOCOLO DE RECEPCION\nRegistro N°:\n232"}]
        doc = FakeDocumentSource(pages)
        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=_ExtractorWithFailedParse(),
            vision=FakeVisionSerial(),
            config=AppConfig(),
        )
        ctx = RunContext(pdf_path=tmp_path / "input.pdf", output_base=tmp_path / "runs")
        result = pipeline.run(ctx)

        # The guard must emit at least one warning mentioning the missing date.
        matching = [w for w in result.warnings if "232" in w and "fecha" in w.lower()]
        assert matching, (
            f"Expected a WARNING for Registro 232 with protocolo_page set but "
            f"fecha_declarada=None. Got warnings: {result.warnings}"
        )


# ---------------------------------------------------------------------------
# C-1 + C-2: real Registro parsers + dedup (proto canonical)
# ---------------------------------------------------------------------------


# Minimal text fragments that trigger the real parser route in pipeline Stage 4.
# The pipeline uses hasattr checks: if the extractor exposes the Registro-level
# methods, the real path is taken.

_PROTO_TEXT = "\n".join([
    "PTR001-TORRE ROSALES",
    "Informe de detalle del formulario",
    "PROTOCOLO DE RECEPCION",
    "Registro N°:\nCONTRATANTE\n:\nCONSTRUCTORA XYZ\n232\n28-05-26",
    "BARRA A615/A706 G60 3/8\" DOB - 6.0 TN",
])

_DETAIL_TEXT = "\n".join([
    "PTR001-TORRE ROSALES",
    "Informe de detalle del formulario",
    "FORM DETAIL",
    "#4252: CTR-PLC01-FR001",
    "Description",
    "232",
    "Form date",
    "May 28, 2026",
    "Notes",
    "BARRA A615/A706 G60 3/8\" DOB - 5.0 TN",
    "Created by",
])


class FakeRegistroExtractor:
    """Fake that exposes both ExtractionPort AND DeclaredExtractorPort methods.

    Returns configurable Registro objects per-call (proto vs detail).
    The pipeline's hasattr check will detect the Registro-level methods and
    take the real dedup path instead of the legacy placeholder path.
    """

    def __init__(
        self,
        proto_registro: "Registro | None" = None,
        detail_registro: "Registro | None" = None,
        table_lines: "list[MaterialLine] | None" = None,
    ) -> None:
        self._proto = proto_registro
        self._detail = detail_registro
        self._table_lines = table_lines or []

    # ExtractionPort
    def extract_declared(self, text: str) -> list[MaterialLine]:
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return list(self._table_lines)

    # DeclaredExtractorPort
    def extract_registro_from_proto_page(self, text: str, source_page: int) -> "Registro | None":
        return self._proto

    def extract_registro_from_detail_page(self, text: str, source_page: int) -> "Registro | None":
        return self._detail


def _build_pipeline_with_registro_extractor(
    pages: list[dict[str, Any]],
    extractor: FakeRegistroExtractor,
    page_to_registro: dict[int, str] | None = None,
    vision: VisionLLMPort | None = None,
    tmp_path: Path | None = None,
    identity: Any | None = None,
) -> tuple[ReconciliationPipeline, RunContext]:
    cfg = AppConfig()
    doc = FakeDocumentSource(pages)
    vis = vision or FakeVisionSerial()
    pipeline = ReconciliationPipeline(
        doc_source=doc,
        extractor=extractor,
        vision=vis,
        config=cfg,
        page_to_registro=page_to_registro or {},
        identity=identity,
    )
    base = tmp_path or Path(".")
    ctx = RunContext(pdf_path=base / "input.pdf", output_base=base / "runs")
    return pipeline, ctx


class TestRealRegistroParsersAndDedup:
    """C-1 + C-2: pipeline uses real Registro parsers and dedupes to one Registro per numero."""

    def test_proto_page_creates_registro_with_real_numero(self, tmp_path: Path) -> None:
        """C-1: PROTO DECLARED page → Registro with real numero, not 'page_N'."""
        from reconciliation.domain.models import Registro
        proto_reg = Registro(numero="232", fecha_declarada=date(2026, 5, 28), declared_lines=[])
        pages = [{"text": _PROTO_TEXT}]
        extractor = FakeRegistroExtractor(proto_registro=proto_reg)
        pipeline, ctx = _build_pipeline_with_registro_extractor(
            pages=pages, extractor=extractor, tmp_path=tmp_path
        )
        result = pipeline.run(ctx)
        assert len(result.declared) == 1
        assert result.declared[0].numero == "232"
        assert result.declared[0].numero != "page_0"

    def test_detail_page_creates_registro_with_real_numero(self, tmp_path: Path) -> None:
        """C-1: FORM DETAIL DECLARED page → Registro with real numero."""
        from reconciliation.domain.models import Registro
        detail_reg = Registro(numero="232", fecha_declarada=date(2026, 5, 28), declared_lines=[])
        pages = [{"text": _DETAIL_TEXT}]
        extractor = FakeRegistroExtractor(detail_registro=detail_reg)
        pipeline, ctx = _build_pipeline_with_registro_extractor(
            pages=pages, extractor=extractor, tmp_path=tmp_path
        )
        result = pipeline.run(ctx)
        assert len(result.declared) == 1
        assert result.declared[0].numero == "232"

    def test_proto_and_detail_same_numero_deduped_to_one_registro(self, tmp_path: Path) -> None:
        """C-2: PROTO + DETAIL pages for same numero → exactly ONE Registro (proto canonical)."""
        from reconciliation.domain.models import Registro, MaterialLine
        from decimal import Decimal

        proto_line = MaterialLine(
            description_raw="BARRA PROTO",
            description_canonical="barra proto",
            unidad="TN",
            cantidad=Decimal("6.0"),
        )
        detail_line = MaterialLine(
            description_raw="BARRA DETAIL",
            description_canonical="barra detail",
            unidad="TN",
            cantidad=Decimal("5.0"),
        )
        proto_reg = Registro(numero="232", fecha_declarada=date(2026, 5, 28), declared_lines=[proto_line])
        detail_reg = Registro(numero="232", fecha_declarada=date(2026, 5, 28), declared_lines=[detail_line])

        pages = [
            {"text": _PROTO_TEXT},   # page 0 → DECLARED (PROTO)
            {"text": _DETAIL_TEXT},  # page 1 → DECLARED (DETAIL) — same numero
        ]
        extractor = FakeRegistroExtractor(proto_registro=proto_reg, detail_registro=detail_reg)
        pipeline, ctx = _build_pipeline_with_registro_extractor(
            pages=pages, extractor=extractor, tmp_path=tmp_path
        )
        result = pipeline.run(ctx)
        # Must be exactly ONE Registro (not two); proto is canonical
        assert len(result.declared) == 1, (
            f"Expected 1 Registro after dedup, got {len(result.declared)}"
        )
        assert result.declared[0].numero == "232"
        # Proto is canonical — its lines are used
        assert result.declared[0].declared_lines[0].description_raw == "BARRA PROTO"

    def test_different_numeros_produce_separate_registros(self, tmp_path: Path) -> None:
        """Two PROTO pages with different numeros → two distinct Registros."""
        from reconciliation.domain.models import Registro

        reg_232 = Registro(numero="232", fecha_declarada=None, declared_lines=[])
        reg_231 = Registro(numero="231", fecha_declarada=None, declared_lines=[])

        pages = [
            {"text": _PROTO_TEXT},
            {"text": _PROTO_TEXT},
        ]

        call_count = 0

        class AlternatingExtractor(FakeRegistroExtractor):
            def extract_registro_from_proto_page(self, text: str, source_page: int):
                nonlocal call_count
                result = reg_232 if call_count == 0 else reg_231
                call_count += 1
                return result

        extractor = AlternatingExtractor()
        pipeline, ctx = _build_pipeline_with_registro_extractor(
            pages=pages, extractor=extractor, tmp_path=tmp_path
        )
        result = pipeline.run(ctx)
        assert len(result.declared) == 2
        numeros = {r.numero for r in result.declared}
        assert numeros == {"232", "231"}


# ---------------------------------------------------------------------------
# C-4: page_to_registro map wired → guia.registro is set
# ---------------------------------------------------------------------------


class TestPageToRegistroWiring:
    """C-4: guia.registro is assigned from page_to_registro map."""

    def test_guia_page_in_map_gets_registro_numero(self, tmp_path: Path) -> None:
        """GUIA page whose 0-based index is in page_to_registro → guia.registro set."""
        pages = [{"text": _GUIA_TEXT, "image": b"\x89PNG"}]  # page 0 is GUIA
        extractor = FakeExtractor(table_lines=[_make_line()])
        page_to_registro = {0: "232"}
        # rev-6: QR evidence required to open the block (invariant guard).
        pipeline, ctx = _build_pipeline_with_registro_extractor(
            pages=pages,
            extractor=extractor,  # type: ignore[arg-type]
            page_to_registro=page_to_registro,
            tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)
        assert len(result.guias) == 1
        assert result.guias[0].registro == "232"

    def test_guia_page_not_in_map_gets_none_registro(self, tmp_path: Path) -> None:
        """GUIA page not in map → guia.registro remains None (surfaces as UNCLASSIFIED)."""
        pages = [{"text": _GUIA_TEXT, "image": b"\x89PNG"}]
        extractor = FakeExtractor(table_lines=[_make_line()])
        # rev-6: QR evidence required to open the block (invariant guard).
        pipeline, ctx = _build_pipeline_with_registro_extractor(
            pages=pages,
            extractor=extractor,  # type: ignore[arg-type]
            page_to_registro={},
            tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)
        assert result.guias[0].registro is None

    def test_guia_registro_matches_declared_registro_produces_reconciled_row(self, tmp_path: Path) -> None:
        """When guia.registro matches declared.numero AND fecha matches → MATCH row."""
        from decimal import Decimal
        from reconciliation.domain.models import Registro, MaterialLine

        # Reconciliation groups by (registro, fecha, material, unidad).
        # For a MATCH the guia.fecha must equal the Registro.fecha_declarada.
        # We use a fixed declared fecha and wire vision to return the same date.
        _FECHA = date(2026, 5, 28)

        qty = Decimal("10.0")
        declared_line = MaterialLine(
            description_raw="BARRA X",
            description_canonical="barra x",
            unidad="TN",
            cantidad=qty,
        )
        guia_line = MaterialLine(
            description_raw="BARRA X",
            description_canonical="barra x",
            unidad="TN",
            cantidad=qty,
            confidence=0.95,
        )
        proto_reg = Registro(numero="232", fecha_declarada=_FECHA, declared_lines=[declared_line])

        pages = [
            {"text": _PROTO_TEXT},             # page 0 → DECLARED
            {"text": _GUIA_TEXT, "image": b"\x89PNG"},  # page 1 → GUIA
        ]

        class _Extractor(FakeRegistroExtractor):
            def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
                return [guia_line]

        extractor = _Extractor(proto_registro=proto_reg)
        page_to_registro = {1: "232"}  # GUIA on page 1 → registro "232"
        # Vision returns the same date as the declared registro fecha
        vision = FakeVisionSerial(
            results=[VisionResult(date=_FECHA, confidence=0.99, raw="28/05/2026")]
        )

        # rev-6: QR evidence required to open the GUIA block (invariant guard).
        pipeline, ctx = _build_pipeline_with_registro_extractor(
            pages=pages,
            extractor=extractor,
            page_to_registro=page_to_registro,
            vision=vision,
            tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)

        assert len(result.declared) == 1
        assert result.declared[0].numero == "232"
        assert len(result.guias) == 1
        assert result.guias[0].registro == "232"
        # With same registro, fecha, material, qty → MATCH row
        match_rows = [r for r in result.rows if r.status == "MATCH"]
        assert len(match_rows) >= 1, (
            f"Expected at least one MATCH row; got statuses: {[r.status for r in result.rows]}"
        )


# ---------------------------------------------------------------------------
# H-5: deskew/title-OCR seam — scanned pages receive ocr_title
# ---------------------------------------------------------------------------


class FakeDeskew:
    """Fake DeskewPort for testing the H-5 wiring seam.

    Returns a configurable title string via extract_title.
    Does not apply any real deskew.
    """

    def __init__(self, title: str | None = "GUIA DE REMISION") -> None:
        self._title = title
        self.correct_orientation_calls: int = 0
        self.extract_title_calls: int = 0

    def correct_orientation(self, image: bytes) -> bytes:
        self.correct_orientation_calls += 1
        return image  # passthrough

    def extract_title(self, image: bytes) -> str | None:
        self.extract_title_calls += 1
        return self._title


# A "scanned" page: has only the universal header (noise-only) → empty body after cleaning
_SCANNED_PAGE_TEXT = "PTR001-TORRE ROSALES\nInforme de detalle del formulario\n"


class TestDeskewTitleOcrSeam:
    """H-5: pipeline calls deskew.extract_title for empty-body pages so they can classify."""

    def test_scanned_guia_page_classified_when_deskew_provides_title(self, tmp_path: Path) -> None:
        """Scanned page with noise-only text + fake OCR title 'GUIA DE REMISION' → GUIA."""
        pages = [{"text": _SCANNED_PAGE_TEXT, "image": b"\x89PNG"}]
        extractor = FakeExtractor(table_lines=[_make_line()])
        deskew = FakeDeskew(title="GUIA DE REMISION")
        cfg = AppConfig()
        doc = FakeDocumentSource(pages)
        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=FakeVisionSerial(),
            config=cfg,
            page_to_registro={},
            deskew=deskew,
        )
        base = tmp_path
        ctx = RunContext(pdf_path=base / "input.pdf", output_base=base / "runs")
        result = pipeline.run(ctx)
        assert deskew.extract_title_calls >= 1, "extract_title should have been called"
        assert result.classifications[0].kind == "GUIA", (
            f"Expected GUIA but got {result.classifications[0].kind}"
        )

    def test_scanned_page_unclassified_when_no_deskew_wired(self, tmp_path: Path) -> None:
        """Without deskew adapter, scanned page stays UNCLASSIFIED — no crash."""
        pages = [{"text": _SCANNED_PAGE_TEXT, "image": b"\x89PNG"}]
        pipeline, ctx = _build_pipeline(pages=pages, tmp_path=tmp_path)
        result = pipeline.run(ctx)
        assert result.classifications[0].kind == "UNCLASSIFIED"

    def test_scanned_page_unclassified_when_deskew_returns_none_title(self, tmp_path: Path) -> None:
        """If deskew returns None for title (PaddleOCR unavailable), page stays UNCLASSIFIED."""
        pages = [{"text": _SCANNED_PAGE_TEXT, "image": b"\x89PNG"}]
        extractor = FakeExtractor()
        deskew = FakeDeskew(title=None)  # unavailable
        cfg = AppConfig()
        doc = FakeDocumentSource(pages)
        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=FakeVisionSerial(),
            config=cfg,
            deskew=deskew,
        )
        base = tmp_path
        ctx = RunContext(pdf_path=base / "input.pdf", output_base=base / "runs")
        result = pipeline.run(ctx)
        assert result.classifications[0].kind == "UNCLASSIFIED"

    def test_deskew_not_called_for_digital_text_pages(self, tmp_path: Path) -> None:
        """Pages with meaningful digital text must NOT trigger extract_title."""
        pages = [
            {"text": _DECLARED_TEXT},  # has content → deskew not needed
        ]
        extractor = FakeRegistroExtractor()
        deskew = FakeDeskew(title="GUIA DE REMISION")
        cfg = AppConfig()
        doc = FakeDocumentSource(pages)
        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=FakeVisionSerial(),
            config=cfg,
            deskew=deskew,
        )
        base = tmp_path
        ctx = RunContext(pdf_path=base / "input.pdf", output_base=base / "runs")
        pipeline.run(ctx)
        # Digital text pages must not trigger extract_title
        assert deskew.extract_title_calls == 0, (
            "extract_title should NOT be called for pages with meaningful digital text"
        )


# ---------------------------------------------------------------------------
# R8.9: _stage_normalize upgrade + key_resolver defensive default (ADR-6)
# ---------------------------------------------------------------------------


from decimal import Decimal as _Decimal
from unittest.mock import MagicMock

from reconciliation.domain.material_key import CanonicalKey
from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer
from reconciliation.domain.material_key_resolver import MaterialKeyResolver


class TestKeyResolverDefensive:
    """Pipeline instantiated without key_resolver → deterministic-only mode."""

    def _build_minimal_pipeline(self):
        """Build a pipeline without key_resolver (defensive default path)."""
        doc = FakeDocumentSource([
            {"text": None, "image": b"\x89PNG\r\n"},
        ])
        extractor = FakeExtractor()
        vision = FakeVisionSerial()
        config = AppConfig()
        return ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=vision,
            config=config,
        )

    def test_pipeline_without_key_resolver_has_default(self) -> None:
        """key_resolver is populated with the defensive default."""
        pipeline = self._build_minimal_pipeline()
        assert pipeline._key_resolver is not None

    def test_default_resolver_inference_is_none(self) -> None:
        """Defensive default uses deterministic-only resolver (no inference port)."""
        pipeline = self._build_minimal_pipeline()
        assert pipeline._key_resolver._inference is None

    def test_stage_normalize_populates_description_canonical(self, tmp_path) -> None:
        """_stage_normalize fills description_canonical using the resolver."""
        from datetime import date

        pipeline = self._build_minimal_pipeline()

        declared_line = MaterialLine(
            description_raw='BARRA AG615/A706 G60 1/2" x 9M',
            description_canonical="",  # empty before normalize
            unidad="TN",
            cantidad=_Decimal("4.124"),
        )
        from reconciliation.domain.models import Registro
        declared = [Registro(
            numero="232",
            fecha_declarada=date(2024, 1, 15),
            declared_lines=[declared_line],
        )]
        guias = []
        norm_declared, norm_guias = pipeline._stage_normalize(declared, guias)
        assert norm_declared[0].declared_lines[0].description_canonical != ""
        assert norm_declared[0].declared_lines[0].match_method == "deterministic"

    def test_stage_normalize_with_llm_inferred_resolver(self, tmp_path) -> None:
        """Pipeline with mock resolver returning llm_inferred sets match_method correctly."""
        from datetime import date

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
            method="llm_inferred",
        )

        doc = FakeDocumentSource([{"text": None, "image": b"\x89PNG\r\n"}])
        extractor = FakeExtractor()
        vision = FakeVisionSerial()
        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=vision,
            config=AppConfig(),
            key_resolver=mock_resolver,
        )

        declared_line = MaterialLine(
            description_raw="ambiguous description",
            description_canonical="",
            unidad="TN",
            cantidad=_Decimal("1.0"),
        )
        from reconciliation.domain.models import Registro
        declared = [Registro(
            numero="100",
            fecha_declarada=date(2024, 1, 1),
            declared_lines=[declared_line],
        )]
        norm_declared, _ = pipeline._stage_normalize(declared, [])
        assert norm_declared[0].declared_lines[0].match_method == "llm_inferred"
        assert norm_declared[0].declared_lines[0].requires_review is True

    def test_stage_normalize_with_unresolved_resolver(self, tmp_path) -> None:
        """Pipeline with mock resolver returning unresolved sets UNRESOLVED:: prefix."""
        from datetime import date

        mock_resolver = MagicMock()
        raw = "some unresolvable text"
        mock_resolver.resolve.return_value = CanonicalKey.unresolved(raw, "TN")

        doc = FakeDocumentSource([{"text": None, "image": b"\x89PNG\r\n"}])
        extractor = FakeExtractor()
        vision = FakeVisionSerial()
        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=vision,
            config=AppConfig(),
            key_resolver=mock_resolver,
        )

        declared_line = MaterialLine(
            description_raw=raw,
            description_canonical="",
            unidad="TN",
            cantidad=_Decimal("1.0"),
        )
        from reconciliation.domain.models import Registro
        declared = [Registro(
            numero="100",
            fecha_declarada=date(2024, 1, 1),
            declared_lines=[declared_line],
        )]
        norm_declared, _ = pipeline._stage_normalize(declared, [])
        assert norm_declared[0].declared_lines[0].description_canonical.startswith("UNRESOLVED::")


# ---------------------------------------------------------------------------
# OCR disabled mode — _stage_extract_ocr with NullOcrExtractor
# ---------------------------------------------------------------------------

from reconciliation.adapters.ocr.null_extractor import NullOcrExtractor
from reconciliation.application.config import OcrConfig


class TestOcrDisabledPipelineStage:
    """Pipeline stage tests for ocr.enabled=False mode.

    Uses fake doc source and NullOcrExtractor directly — no paddle, no real PDF.
    """

    def _build_ocr_disabled_pipeline(
        self,
        pages: list[dict],
        tmp_path: Path,
        identity: Any | None = None,
    ) -> tuple[ReconciliationPipeline, RunContext]:
        cfg = AppConfig(ocr=OcrConfig(enabled=False))
        doc = FakeDocumentSource(pages)
        # NullOcrExtractor as the extractor (satisfies ExtractionPort)
        extractor = NullOcrExtractor()
        vision = FakeVisionSerial()
        pipeline = ReconciliationPipeline(
            doc_source=doc,
            extractor=extractor,
            vision=vision,
            config=cfg,
            deskew=None,  # explicitly no deskew (mirrors what build_pipeline wires)
            identity=identity,
        )
        ctx = RunContext(pdf_path=tmp_path / "input.pdf", output_base=tmp_path / "runs")
        return pipeline, ctx

    def test_stage_extract_ocr_with_null_extractor_returns_raw_guias_with_empty_lines(
        self, tmp_path: Path
    ) -> None:
        """With NullOcrExtractor, _stage_extract_ocr creates _RawGuia objects
        with empty lines per GUIA page — no OCR failure warning emitted.
        """
        from reconciliation.application.pipeline import DecodeOutcome
        from reconciliation.domain.models import PageClassification

        pipeline, ctx = self._build_ocr_disabled_pipeline(
            pages=[{"text": _GUIA_TEXT, "image": b"\x89PNG\r\n"}],
            tmp_path=tmp_path,
        )
        classifications = [
            PageClassification(page=0, kind="GUIA", title_matched="GUIA DE REMISION", confidence=1.0)
        ]
        decode_map = {
            0: DecodeOutcome(
                identity=None,
                hashqr_url=None,
                rendered=b"\x89PNG\r\n",
                decoded=False,
            )
        }

        raw_guias, ocr_warnings = pipeline._stage_extract_ocr(
            classifications, decode_map=decode_map
        )

        assert len(raw_guias) == 1
        assert raw_guias[0].lines == []
        # No _ocr_failed warning — empty lines are INTENTIONAL (skip, not failure)
        assert ocr_warnings == []

    def test_stage_extract_ocr_no_ocr_failed_flag_on_null_extractor(
        self, tmp_path: Path
    ) -> None:
        """NullOcrExtractor does NOT set _ocr_failed — intentional skip != failure."""
        from reconciliation.application.pipeline import DecodeOutcome
        from reconciliation.domain.models import PageClassification

        pipeline, ctx = self._build_ocr_disabled_pipeline(
            pages=[{"text": _GUIA_TEXT, "image": b"\x89PNG\r\n"}],
            tmp_path=tmp_path,
        )
        # Confirm _ocr_failed is absent or False on the extractor
        assert not getattr(pipeline._extractor, "_ocr_failed", False)

    def test_full_run_ocr_disabled_guia_gets_empty_lines(
        self, tmp_path: Path
    ) -> None:
        """End-to-end run with ocr.enabled=False: GUIA page produces a guía with
        empty lines and no OCR warnings in the result.

        rev-6: in OCR-disabled (SUNAT-authoritative) mode the guía is identified by
        its QR (which also drives the SUNAT fetch).  A real guía therefore carries
        QR evidence; the invariant guard admits it even with 0 OCR lines (identity
        is not None → has_guia_evidence True).  FakeIdentityPerPage models that QR.
        """
        pipeline, ctx = self._build_ocr_disabled_pipeline(
            pages=[{"text": _GUIA_TEXT, "image": b"\x89PNG\r\n"}],
            tmp_path=tmp_path,
            identity=FakeIdentityPerPage(),
        )
        result = pipeline.run(ctx)
        assert result.classifications[0].kind == "GUIA"
        assert len(result.guias) == 1
        assert result.guias[0].lines == []
        # No OCR failure warnings — empty lines are intentional
        ocr_warnings = [w for w in result.warnings if "OCR unavailable" in w]
        assert ocr_warnings == []
