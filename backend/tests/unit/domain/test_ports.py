"""Unit tests for port Protocol structural compliance (task 1.5).

Each test creates a minimal stub implementing the Protocol and verifies
that isinstance() check passes (runtime_checkable=True).

Rev-2 additions: IdentityExtractionPort, SunatGreFetchPort (seam only).
"""

from __future__ import annotations

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
    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
        return GuiaIdentity(
            serie="T009",
            numero="0741770",
            ruc_emisor="20370146994",
            ruc_receptor="20613231871",
            tipo="09",
            confidence=1.0,
        )

    def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None:
        return None


class _StubSunatGreFetch:
    def fetch(self, hashqr_url: str) -> None:
        return None

    def fetch_many(self, urls: list[str], concurrency: int = 5) -> dict:
        return {url: self.fetch(url) for url in urls}


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
            def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
                return None

            def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None:
                return None

        stub = _NoneStub()
        assert isinstance(stub, IdentityExtractionPort)
        assert stub.decode_identity(b"img") is None

    # T-1 (REV-R01): decode_hashqr_url must be a formal Protocol method.
    def test_decode_hashqr_url_required_in_protocol(self) -> None:
        """A stub missing decode_hashqr_url does NOT satisfy IdentityExtractionPort.

        This test is the RED gate: it will fail until decode_hashqr_url is added
        to the IdentityExtractionPort Protocol definition.
        """
        class _MissingHashqr:
            # Only decode_identity — no decode_hashqr_url
            def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
                return None

        stub = _MissingHashqr()
        # After T-1 promotion, isinstance MUST be False for this stub.
        assert not isinstance(stub, IdentityExtractionPort)

    def test_decode_hashqr_url_full_stub_satisfies_protocol(self) -> None:
        """A stub with both methods fully satisfies IdentityExtractionPort (GREEN gate)."""
        class _FullStub:
            def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
                return None

            def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None:
                return "https://e-factura.sunat.gob.pe/v1/gre?hashqr=TOKEN"

        stub = _FullStub()
        assert isinstance(stub, IdentityExtractionPort)
        assert stub.decode_hashqr_url(b"image") is not None

    def test_decode_hashqr_url_can_return_none(self) -> None:
        """decode_hashqr_url returns None when no URL-variant QR is found."""
        class _NoneStub:
            def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None:
                return None

            def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None:
                return None

        stub = _NoneStub()
        assert isinstance(stub, IdentityExtractionPort)
        assert stub.decode_hashqr_url(b"img") is None


class TestSunatGreFetchPort:
    def test_isinstance_check_passes(self) -> None:
        stub = _StubSunatGreFetch()
        assert isinstance(stub, SunatGreFetchPort)

    def test_fetch_returns_none_when_disabled(self) -> None:
        stub = _StubSunatGreFetch()
        assert stub.fetch("https://example.com/hashqr=XYZ") is None

    def test_fetch_many_default_delegates_to_fetch(self) -> None:
        """R10.7: SunatGreFetchPort.fetch_many default loops fetch() for each URL."""
        stub = _StubSunatGreFetch()
        urls = ["url-1", "url-2"]
        result = stub.fetch_many(urls)
        assert set(result.keys()) == {"url-1", "url-2"}
        assert all(v is None for v in result.values())

    def test_fetch_many_empty_urls(self) -> None:
        stub = _StubSunatGreFetch()
        assert stub.fetch_many([]) == {}


# ---------------------------------------------------------------------------
# R8.6: MaterialInferencePort and MaterialKeyInference (MAT-006)
# ---------------------------------------------------------------------------

from reconciliation.domain.models import MaterialKeyInference
from reconciliation.domain.ports import MaterialInferencePort


class _StubMaterialInference:
    def infer(self, description: str) -> MaterialKeyInference | None:
        return MaterialKeyInference(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            confidence=0.95,
        )


class TestMaterialInferencePort:
    def test_isinstance_check_passes(self) -> None:
        stub = _StubMaterialInference()
        assert isinstance(stub, MaterialInferencePort)

    def test_infer_returns_material_key_inference(self) -> None:
        stub = _StubMaterialInference()
        result = stub.infer("some description")
        assert isinstance(result, MaterialKeyInference)
        assert result.familia == "BARRA"

    def test_infer_can_return_none(self) -> None:
        class _NoneStub:
            def infer(self, description: str) -> MaterialKeyInference | None:
                return None

        stub = _NoneStub()
        assert isinstance(stub, MaterialInferencePort)
        assert stub.infer("unknown") is None


class TestMaterialKeyInference:
    def test_all_fields(self) -> None:
        inf = MaterialKeyInference(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            confidence=0.9,
        )
        assert inf.familia == "BARRA"
        assert inf.grado == "A615 G60"
        assert inf.diametro == '1/2"'
        assert inf.presentacion == "9M"
        assert inf.confidence == 0.9

    def test_optional_fields_default_none(self) -> None:
        inf = MaterialKeyInference(familia="BARRA")
        assert inf.grado is None
        assert inf.diametro is None
        assert inf.presentacion is None
        assert inf.confidence == 0.0
