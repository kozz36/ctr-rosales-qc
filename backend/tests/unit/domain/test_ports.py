"""Unit tests for port Protocol structural compliance (task 1.5).

Each test creates a minimal stub implementing the Protocol and verifies
that isinstance() check passes (runtime_checkable=True).

Rev-2 additions: IdentityExtractionPort, SunatGreFetchPort (seam only).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from reconciliation.domain.models import GuiaIdentity, MaterialLine, ReconciliationRow, VisionResult
from reconciliation.domain.ports import (
    DocumentSourcePort,
    ExtractionPort,
    IdentityExtractionPort,
    ReportPort,
    SunatGreFetchPort,
    VisionLLMPort,
)


class _StubDocumentSource:
    def page_count(self) -> int:
        return 2

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return b"PNG_BYTES"

    def page_text(self, idx: int) -> str | None:
        return "some text"


class _StubExtraction:
    def extract_declared(self, text: str) -> list[MaterialLine]:
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        return []


class _StubVisionLLM:
    supports_batch: bool = True

    def read_handwritten_date(
        self,
        image: bytes,
        hint: str | None = None,
    ) -> VisionResult:
        return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(
        self,
        images: list[bytes],
    ) -> list[VisionResult]:
        return []


class _StubReport:
    def export(
        self,
        rows: list[ReconciliationRow],
        audit_trail: list[dict],  # type: ignore[type-arg]
        dst: Path,
        fmt: Literal["xlsx", "csv"],
    ) -> Path:
        return dst


class TestDocumentSourcePort:
    def test_isinstance_check_passes(self) -> None:
        stub = _StubDocumentSource()
        assert isinstance(stub, DocumentSourcePort)

    def test_page_count_callable(self) -> None:
        stub = _StubDocumentSource()
        assert stub.page_count() == 2

    def test_render_page_returns_bytes(self) -> None:
        stub = _StubDocumentSource()
        result = stub.render_page(0)
        assert isinstance(result, bytes)

    def test_page_text_returns_str_or_none(self) -> None:
        stub = _StubDocumentSource()
        assert isinstance(stub.page_text(0), (str, type(None)))


class TestExtractionPort:
    def test_isinstance_check_passes(self) -> None:
        stub = _StubExtraction()
        assert isinstance(stub, ExtractionPort)

    def test_extract_declared_returns_list(self) -> None:
        stub = _StubExtraction()
        assert stub.extract_declared("text") == []

    def test_extract_printed_table_returns_list(self) -> None:
        stub = _StubExtraction()
        assert stub.extract_printed_table(b"img") == []


class TestVisionLLMPort:
    def test_isinstance_check_passes(self) -> None:
        stub = _StubVisionLLM()
        assert isinstance(stub, VisionLLMPort)

    def test_supports_batch_attribute_present(self) -> None:
        stub = _StubVisionLLM()
        assert isinstance(stub.supports_batch, bool)

    def test_read_handwritten_date_returns_vision_result(self) -> None:
        stub = _StubVisionLLM()
        result = stub.read_handwritten_date(b"image")
        assert isinstance(result, VisionResult)

    def test_batch_returns_list(self) -> None:
        stub = _StubVisionLLM()
        assert stub.read_handwritten_date_batch([b"img1", b"img2"]) == []


class TestReportPort:
    def test_isinstance_check_passes(self) -> None:
        stub = _StubReport()
        assert isinstance(stub, ReportPort)

    def test_export_returns_path(self) -> None:
        stub = _StubReport()
        dst = Path("/tmp/out")
        result = stub.export([], [], dst, "xlsx")
        assert result == dst


# ---------------------------------------------------------------------------
# Rev-2 ports (S1.1)
# ---------------------------------------------------------------------------


class _StubIdentityExtraction:
    def decode_identity(self, image: bytes) -> GuiaIdentity | None:
        return GuiaIdentity(
            serie="T009",
            numero="0741770",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="09",
            confidence=1.0,
        )


class _StubSunatGreFetch:
    def fetch(self, hashqr_url: str) -> None:
        return None


class TestIdentityExtractionPort:
    def test_isinstance_check_passes(self) -> None:
        stub = _StubIdentityExtraction()
        assert isinstance(stub, IdentityExtractionPort)

    def test_decode_identity_returns_guia_identity(self) -> None:
        stub = _StubIdentityExtraction()
        result = stub.decode_identity(b"image")
        assert isinstance(result, GuiaIdentity)
        assert result.guia_id == "T009-0741770"

    def test_decode_identity_can_return_none(self) -> None:
        class _NoneStub:
            def decode_identity(self, image: bytes) -> GuiaIdentity | None:
                return None

        stub = _NoneStub()
        assert isinstance(stub, IdentityExtractionPort)
        assert stub.decode_identity(b"img") is None


class TestSunatGreFetchPort:
    def test_isinstance_check_passes(self) -> None:
        stub = _StubSunatGreFetch()
        assert isinstance(stub, SunatGreFetchPort)

    def test_fetch_returns_none_when_disabled(self) -> None:
        stub = _StubSunatGreFetch()
        assert stub.fetch("https://example.com/hashqr=XYZ") is None
