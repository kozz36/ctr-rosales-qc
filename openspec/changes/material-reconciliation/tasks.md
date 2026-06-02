# Tasks — material-reconciliation

**Change**: `material-reconciliation` · **Phase**: tasks · **Store**: hybrid · **Date**: 2026-06-02 (refreshed)

Greenfield build + rev-2 delta. Hexagonal architecture (domain / application / adapters / infrastructure). All locked decisions honored: MATCH tolerance EXACT(0), confidence auto-flag 0.85, deskew guía-only + orientation fallback, review persistence per-run sidecar review.json, xlsx 10-column set + summary sheet, provider-agnostic VisionLLMPort, QR-tiered identity extraction. `strict_tdd: false` — tests are included (standard mode).

**State as of 2026-06-02:** Phases 0–5 COMPLETE (49/55 greenfield tasks done). Rev-2 delta adds slices 1 and 2 (backend and frontend hotfixes). Phase 6/7 (e2e + hardening) remains for both greenfield and rev-2 scope.

---

## Phase 0 — Project Scaffolding

> Sequential. Must complete before any other phase. Parallelism within 0.x where noted.

### [x] 0.1 — Backend Python package scaffold

**Spec refs**: design §1 (folder structure), config.yaml tasks rules.
**Deliverables**:
- `backend/pyproject.toml` with `[project]` (name=reconciliation, python=3.12), `[tool.ruff]` (line-length=100, select=["E","F","I","UP"]), `[tool.mypy]` (strict=true, ignore_missing_imports=true), `[tool.pytest.ini_options]` (testpaths=["tests"], addopts="-q").
- `backend/src/reconciliation/__init__.py` (empty sentinel).
- Directory tree created (empty `__init__.py` files): `domain/`, `application/`, `adapters/pdf/`, `adapters/ocr/`, `adapters/vision/`, `adapters/report/`, `infrastructure/api/`, `tests/unit/`, `tests/integration/`.
- `backend/.python-version` pinned to 3.12.
- `backend/README.md` with one-sentence description.

**Completable in**: one session.

### [x] 0.2 — Backend dependency pinning

**Spec refs**: design §1, §3, §6.
**Depends on**: 0.1.
**Deliverables**:
- `pyproject.toml` `[project.dependencies]`: `fastapi>=0.111`, `uvicorn[standard]`, `pydantic>=2.7`, `pydantic-settings>=2.2`, `pymupdf>=1.24`, `paddlepaddle`, `paddleocr`, `anthropic>=0.26`, `openai>=1.30`, `openpyxl>=3.1`, `polars>=0.20`, `python-multipart`.
- `[project.optional-dependencies]` `dev`: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`, `types-openpyxl`.
- Lock file generated (`uv lock` or `poetry lock`).

**Completable in**: one session.

### [x] 0.3 — Frontend Vue 3 + Vite + TypeScript skeleton

**Spec refs**: design §1 (frontend folder structure), §5 (review UX).
**Parallel with**: 0.2 (independent of backend dependencies).
**Deliverables**:
- `frontend/` created via `npm create vue@latest` or manual: `package.json` (vue@3, vite, typescript, @vitejs/plugin-vue).
- Dependencies added: `pinia`, `@tanstack/vue-query`, `primevue`, `tailwindcss`, `postcss`, `autoprefixer`, `vitest`, `@testing-library/vue`, `@vue/test-utils`.
- `frontend/src/app/main.ts`, `App.vue`, `router.ts` (Vue Router, two placeholder routes: `/` run upload, `/runs/:id` review).
- `frontend/src/design/tokens.css` with empty variable blocks.
- `frontend/src/api/client.ts` (axios or fetch wrapper, baseURL from env), `types.ts` (empty exports file).
- `frontend/src/stores/reconciliation.ts`, `stores/run.ts` (empty Pinia stores, defineStore only).
- `frontend/src/composables/useReconciliationApi.ts` (empty composable stub).
- Empty feature directories: `features/review/`, `features/run/`.
- `frontend/vite.config.ts`, `tsconfig.json`, `tailwind.config.ts`.

**Completable in**: one session.

### [x] 0.4 — CI / lint baseline

**Spec refs**: config.yaml rules (greenfield).
**Depends on**: 0.1, 0.3.
**Deliverables**:
- `backend/Makefile` targets: `lint` (ruff check), `typecheck` (mypy src/), `test` (pytest).
- `frontend/package.json` scripts: `lint` (eslint), `typecheck` (vue-tsc), `test` (vitest run).
- `pyproject.toml` ruff and mypy configs verified passing on empty package (no errors).

**Completable in**: one session.

---

## Phase 1 — Domain Core

> Tasks 1.1–1.4 are independent of each other and can run in parallel after Phase 0 completes.

### [x] 1.1 — Domain data models

**Spec refs**: design §7, REC-001, REC-002, EXT-010.
**Depends on**: 0.1.
**Deliverables** (`backend/src/reconciliation/domain/models.py`):
- `MaterialLine(BaseModel)`: `description_raw: str`, `description_canonical: str`, `unidad: Literal["KG","TN","RD","Rollo"]`, `cantidad: Decimal`, `confidence: float | None = None`, `source_page: int | None = None`.
- `GuiaDeRemision(BaseModel)`: `guia_id: str`, `registro: str | None`, `fecha: date | None`, `fecha_confidence: float | None`, `lines: list[MaterialLine]`, `source_pages: list[int]`.
- `Registro(BaseModel)`: `numero: str`, `fecha_declarada: date | None`, `declared_lines: list[MaterialLine]`.
- `PageClassification(BaseModel)`: `page: int`, `kind: Literal["GUIA","DECLARED","IGNORED","UNCLASSIFIED"]`, `title_matched: str | None`, `confidence: float`.
- `ReconciliationRow(BaseModel)`: `registro: str`, `fecha: date | None`, `material_canonical: str`, `unidad: str`, `declared_qty: Decimal`, `summed_qty: Decimal`, `delta: Decimal`, `status: Literal["MATCH","MISMATCH","DECLARED_MISSING","GUIA_MISSING","UNCLASSIFIED"]`, `source_pages: list[int]`, `min_confidence: float | None`.
- `VisionResult(BaseModel)`: `date: date | None`, `confidence: float`, `raw: str`.
- `domain/errors.py`: `ReconciliationError`, `IngestionError`, `ExtractionError`, `VisionCapExceededError` (all `Exception` subclasses with structured `detail: dict`).

**Tests** (`tests/unit/domain/test_models.py`): model instantiation, `Decimal` precision, `None` confidence allowed.

**Completable in**: one session.

### [x] 1.2 — PageClassifier

**Spec refs**: EXT-001, EXT-002, INJ-007 (orientation fallback feeds back here).
**Depends on**: 1.1.
**Deliverables** (`backend/src/reconciliation/domain/classifier.py`):
- `TITLE_RULES: dict[str, Literal["GUIA","DECLARED","IGNORED","UNCLASSIFIED"]]` — exact title strings: `"GUÍA DE REMISIÓN"→"GUIA"`, `"PLANILLA RESUMEN"→"IGNORED"`, `"LISTADO DE BARRAS"→"IGNORED"`, `"PROTOCOLO DE RECEPCIÓN"→"DECLARED"`, `"DETALLE"→"DECLARED"`, `"CARÁTULA"→"IGNORED"`, plus case-insensitive match.
- `PageClassifier.classify(page_text: str | None, ocr_title: str | None) -> PageClassification`: normalizes title to uppercase-stripped, matches rules, returns `UNCLASSIFIED` with low confidence when no match. MUST NOT use supplier name.
- `LOW_CONFIDENCE_THRESHOLD: float = 0.85` (module constant).

**Tests** (`tests/unit/domain/test_classifier.py`): each title variant, empty → UNCLASSIFIED, case-insensitive, supplier name → UNCLASSIFIED.

**Completable in**: one session.

### [x] 1.3 — MaterialNormalizer

**Spec refs**: REC-001, REC-002, EXT-010.
**Depends on**: 1.1.
**Deliverables** (`backend/src/reconciliation/domain/normalizer.py`).

**Completable in**: one session.

### [x] 1.4 — ReconciliationService

**Spec refs**: REC-001 through REC-010, REC-S01 through REC-S08.
**Depends on**: 1.1, 1.3.
**Deliverables** (`backend/src/reconciliation/domain/reconciliation.py`).

**Completable in**: one session.

### [x] 1.5 — Port definitions

**Spec refs**: design §2, EXT-006, EXT-007, EXT-009, EXP-007.
**Depends on**: 1.1.
**Parallel with**: 1.2, 1.3, 1.4.
**Deliverables** (`backend/src/reconciliation/domain/ports.py`):
- `DocumentSourcePort`, `ExtractionPort`, `VisionLLMPort`, `ReportPort` — all `typing.Protocol` with `runtime_checkable=True`.

**Tests** (`tests/unit/domain/test_ports.py`): structural compliance.

**Completable in**: one session.

---

## Phase 2 — Application Layer

> 2.1 → 2.2 → 2.3 sequential. 2.4 parallel with 2.1.

### [x] 2.1 — AppConfig (pydantic-settings)

**Spec refs**: design §3, EXT-006, EXT-008, INJ-007.
**Depends on**: Phase 0.
**Deliverables** (`backend/src/reconciliation/application/config.py`).

**Completable in**: one session.

### [x] 2.2 — RunContext

**Spec refs**: INJ-003, INJ-008, REV-008.
**Depends on**: 2.1.
**Deliverables** (`backend/src/reconciliation/application/run_context.py`).

**Completable in**: one session.

### [x] 2.3 — ReconciliationPipeline

**Spec refs**: design §4, INJ-001 through INJ-009, EXT-001 through EXT-011, REC-001 through REC-010.
**Depends on**: 1.1–1.5, 2.1, 2.2.
**Deliverables** (`backend/src/reconciliation/application/pipeline.py`).

**Completable in**: one session (one long session).

### [x] 2.4 — ReviewService

**Spec refs**: REV-001 through REV-009, REC-006.
**Depends on**: 1.4, 2.2.
**Parallel with**: 2.3.
**Deliverables** (`backend/src/reconciliation/application/review_service.py`).

**Completable in**: one session.

---

## Phase 3 — Adapters

> 3.1–3.5 are independent and can run in parallel once Phase 1 (ports) is done. 3.6 depends on 3.5.

### [x] 3.1 — PdfStructureAdapter (PyMuPDF)

**Spec refs**: INJ-001 through INJ-009, design §2 `DocumentSourcePort`.
**Depends on**: 1.5.

**Completable in**: one session.

### [x] 3.2 — DeskewAdapter (PaddleOCR)

**Spec refs**: INJ-007, design §1 `DeskewAdapter`.
**Depends on**: 1.5.

**Completable in**: one session.

### [x] 3.3 — PrintedTableAdapter (PaddleOCR)

**Spec refs**: EXT-004, EXT-009, EXT-010, design §2 `ExtractionPort.extract_printed_table`.
**Depends on**: 1.5.

**Completable in**: one session.

### [x] 3.4 — DigitalTextExtractionAdapter

**Spec refs**: EXT-003, EXT-009, INJ-006.
**Depends on**: 1.5.

**Completable in**: one session.

### [x] 3.5 — Vision adapters + factory

**Spec refs**: EXT-005, EXT-006, EXT-007, EXT-008, EXT-011, design §3.
**Depends on**: 1.5.

**Completable in**: one session.

### [x] 3.6 — ExcelReportAdapter

**Spec refs**: EXP-001 through EXP-008, design §1 `ExcelReportAdapter`.
**Depends on**: 1.5, 1.1.

**Completable in**: one session.

---

## Phase 4 — Infrastructure / Wiring

> 4.1 → 4.2 sequential. 4.1 can start once Phase 3 adapters are done.

### [x] 4.1 — Composition root (container.py)

**Spec refs**: design §1 infrastructure, EXT-006, EXP-007.
**Depends on**: 2.1, 2.3, 2.4, 3.1–3.6.

**Completable in**: one session.

### [x] 4.2 — FastAPI application + routes

**Spec refs**: design §6 (API surface), REV-001 through REV-009, EXP-001 through EXP-008.
**Depends on**: 4.1.

**Completable in**: one session (may be a long session).

---

## Phase 5 — Frontend Features

> 5.1 → 5.2 → 5.3 → 5.4 → 5.5 loosely sequential. 5.1 and 5.2 can parallel.

### [x] 5.1 — Design tokens + API client

**Spec refs**: design §5 (tokens), design §6 (API surface).
**Depends on**: Phase 0.3.

**Completable in**: one session.

### [x] 5.2 — Run upload + progress (features/run)

**Spec refs**: design §4 (pipeline sequence from UI perspective), design §5.
**Depends on**: 5.1, Phase 0.3.

**Completable in**: one session.

### [x] 5.3 — ReviewGrid + ReconciliationRow

**Spec refs**: REV-001, REV-002, REV-004, REV-005, REV-006, REV-007, design §5.
**Depends on**: 5.1.

**Completable in**: one session (long).

### [x] 5.4 — GuiaReassignDialog + ExportButton

**Spec refs**: REV-003, REV-007, EXP-001 through EXP-008.
**Depends on**: 5.3.

**Completable in**: one session.

### [x] 5.5 — Review page route wiring

**Spec refs**: REV-001 through REV-009, design §5.
**Depends on**: 5.2, 5.3, 5.4.

**Completable in**: one session.

---

## Slice 1 — Backend Hotfix (rev-2 delta)

> **Sequential within slice.** Must complete before slice 2. Sub-tasks are sequentially ordered by dependency; parallelism noted where safe.
>
> Maps design delta §A (identity port + QR adapter), §B (block grouping), §C (authoritative fecha), §D (guía-contribution model), §E (UNRESOLVED fallback), §F (fixture correction).

### [x] S1.1 — `IdentityExtractionPort` + `GuiaIdentity` domain types

**Spec refs**: EXT-011, EXT-012, EXT-013.
**Depends on**: 1.5 (existing ports.py), 1.1 (existing models.py).
**Deliverables**:
- `domain/ports.py`: add `IdentityExtractionPort(Protocol)`: `decode_identity(image: bytes) -> GuiaIdentity | None`.
- `domain/models.py`: add `GuiaIdentity(BaseModel)`: `guia_id: str`, `ruc_emisor: str`, `ruc_receptor: str`, `tipo: str`, `hashqr_url: str | None`, `confidence: float`.
- Update `GuiaDeRemision`: add `ruc_emisor: str | None`, `ruc_receptor: str | None`, `tipo: str | None`, `gre_hashqr_url: str | None`, `identity_confidence: float`, `identity_source: Literal["qr", "ocr_fallback"]`, `first_page: int`.
- Add `GuiaContribution(BaseModel)`: `guia_id: str`, `source_pages: list[int]`, `cantidad: Decimal`, `unidad: str`, `confidence: float`, `identity_source: Literal["qr", "ocr_fallback"]`.
- Update `ReconciliationRow`: add `guias: list[GuiaContribution]` (inline; `summed_qty` becomes derived — computed as `sum(g.cantidad for g in guias)`).
- Add `SunatGreFetchPort(Protocol)` seam: `fetch(hashqr_url: str) -> OfficialGre | None` (off by default).
- `domain/errors.py`: add `IdentityDecodeError` (logged-only, not raised — QR failures degrade to OCR fallback).

**Tests** (`tests/unit/domain/test_models.py` updated + `tests/unit/domain/test_ports.py` updated):
- `GuiaIdentity` model instantiation; `GuiaContribution` with all fields; `ReconciliationRow.summed_qty` is the sum of `guias[*].cantidad` (property test).
- `IdentityExtractionPort` structural compliance stub.
- `ReconciliationRow` model rejects direct `summed_qty` mutation (if implemented as property).

**Parallelism**: independent of S1.2–S1.6; proceed to S1.2 immediately after.
**Completable in**: one session.

### [x] S1.2 — `QrBarcodeExtractionAdapter` (local decode)

**Spec refs**: EXT-011, EXT-012, EXT-013.
**Depends on**: S1.1 (`IdentityExtractionPort`, `GuiaIdentity`).
**Deliverables** (`backend/src/reconciliation/adapters/identity/qr_barcode.py`):
- `QrBarcodeExtractionAdapter(IdentityExtractionPort)`:
  - `decode_identity(image: bytes) -> GuiaIdentity | None`.
  - Renders via PyMuPDF at 150 DPI × 2× grayscale upscale (≈300 DPI effective). Uses the passed `image` bytes directly — caller already rendered; adapter calls the grayscale/upscale transform on the bytes.
  - **Decoder union**: attempts `pyzbar.decode` AND `zxingcpp.read_barcodes` (both must be tried; result = union). Lazy-imports both inside the method body — NEVER at module level (test suite must run without pyzbar/zxing-cpp installed).
  - **Compact GRE QR parse**: pipe-delimited positional format `RUC_emisor|tipo|serie|numero|doc_type|RUC_receptor`. Field extraction by index 0–5 (position-defensive). `guia_id = f"{serie}-{numero}"`.
  - **URL-variant QR**: payload beginning with `http://` or `https://` and containing `hashqr=` → stored in `GuiaIdentity.hashqr_url`; NOT parsed as data QR.
  - **Confidence gate**: `confidence = 1.0` iff ALL: `ruc_emisor` and `ruc_receptor` are exactly 11 numeric digits, `tipo ∈ {"09", "31"}`, `serie` non-empty, `numero` non-empty. Any failure → return `None`; log failure to audit.
  - Performance target: ≤ 200 ms/page.
- Add `pyzbar` and `zxing-cpp` to `pyproject.toml` `[project.optional-dependencies]` under a new `identity` extra (NOT in default deps).

**Tests** (`tests/unit/adapters/test_qr_barcode.py`):
- Happy path: image with compact GRE QR → `GuiaIdentity{guia_id="T009-0741770", ruc_emisor="20370146994", ruc_receptor="20613231871", tipo="09", confidence=1.0}` (EXT-S13).
- 10-digit RUC → `None` returned; failure logged (EXT-S14).
- URL-variant QR detected → `hashqr_url` populated, not parsed as data QR.
- Both decoders mocked (pyzbar returns nothing, zxing-cpp returns the QR) → union works.
- Lazy-import: importing the module with pyzbar absent does NOT raise ImportError at module load time.
- **Risk-3 defensive scenario**: image with ONLY a URL-variant QR and no compact data QR → adapter returns `None` gracefully (no crash, fallback to OCR identity).

**Completable in**: one session.

### [x] S1.3 — `SectionIdPredicate` (section-ID guard)

**Spec refs**: EXT-018, REC-C07, design §E.
**Depends on**: S1.1 (domain layer stable).
**Deliverables** (`backend/src/reconciliation/domain/section_id_guard.py` or inline utility):
- Define a predicate `is_section_id(value: str) -> bool` that returns `True` when the string matches the known section-ID pattern from the real PDF Contents (e.g., numeric values in the 4-digit range, pattern `4[0-9]{3}` or a concrete inclusion set derived from the PDF TOC). Implementation MUST be expressed as a configurable predicate (not hardcoded) so it stays valid if the PDF TOC changes.
- Used by `build_page_to_registro_map` and `_derive_numero` to guard against emitting a section ID as a registro number.
- Used in tests (EXT-S19, EXT-S20, REC-C07) to assert that no `GuiaDeRemision.registro` value passes the predicate.

**Tests** (`tests/unit/domain/test_section_id_guard.py`):
- Known section IDs (e.g., `"4252"`, `"4251"`) → `is_section_id` returns `True`.
- Real registro numbers (e.g., `"232"`, `"231"`, `"100"`) → returns `False`.
- Empty string, None → returns `False` (no crash).

**Parallelism**: can be developed alongside S1.2 (no dependency between them).
**Completable in**: one session (small).

### [x] S1.4 — `_derive_numero` / `build_page_to_registro_map` UNRESOLVED fix

**Spec refs**: EXT-018, EXT-S19, EXT-S20, REC-C05, REC-C06, REC-C07, design §E.
**Depends on**: S1.1 (models), S1.3 (section-ID predicate).
**Deliverables** (modify `backend/src/reconciliation/application/pipeline.py` or `domain/reconciliation.py` wherever these functions live):
- `_derive_numero(section_id, section_map) -> str | None`: returns the Registro N° string if derivable; returns `None` if not derivable; MUST NOT return a value for which `is_section_id(value)` is `True`.
- `build_page_to_registro_map(contents_offsets, guia_pages) -> dict[int, str | None]`: maps each guía page index to its Registro N° or `None` (UNRESOLVED).
- Sentinel format: when returning a sentinel for audit, use `"UNRESOLVED:<source_section_id>"` (preserves the section ID for traceability without using it as a business key).
- Unresolved guías (`registro is None`) collected in `ReconciliationResult.unresolved_guias: list[GuiaDeRemision]` on the output structure.

**Tests** (update `tests/unit/application/test_pipeline.py` or add `tests/unit/domain/test_registro_map.py`):
- Section ID input → returns `None` (EXT-S20 assertion: `GuiaDeRemision.registro` is never `"4252"`).
- Valid mapping → returns Registro N° string.
- No mapping found → returns `None`, guía appears in `unresolved_guias`.
- `ReconciliationRow` with `registro = "4252"` NEVER produced (regression guard).

**Completable in**: one session.

### [x] S1.5 — Multi-page guía block grouping (pipeline stage §B)

**Spec refs**: EXT-015, EXT-S15, EXT-S16, EXT-S17, EXT-S18, design §B.
**Depends on**: S1.2 (QR adapter), S1.4 (UNRESOLVED fix), S1.1 (updated models).
**Deliverables** (modify `backend/src/reconciliation/application/pipeline.py`):
- Insert new pipeline stage after classify+deskew and before OCR/vision extraction: **assemble guía blocks**.
- Algorithm:
  1. Iterate `guia`-classified pages in order.
  2. Attempt `IdentityExtractionPort.decode_identity(image)` per page.
  3. Start new `GuiaDeRemision` block on: (a) run-start, (b) section boundary cross (from contents map), (c) successful QR decode with a `guia_id` different from the current block's.
  4. Propagate `guia_id`, `ruc_emisor`, `ruc_receptor`, `tipo`, `gre_hashqr_url`, `identity_confidence`, `identity_source` from the first page of each block to all continuation pages.
  5. Append OCR-extracted `MaterialLine` rows from continuation pages to the same `GuiaDeRemision.lines`.
  6. Set `first_page` to the page index of the block's first page.
- Remove `guia_id = f"guia_page_{n}"` per-page assignment from the pipeline — this naming scheme MUST NOT appear in any `GuiaDeRemision` produced after this delta.
- OCR fallback identity: when `decode_identity` returns `None`, derive `guia_id` from visible header text (existing OCR path); set `identity_source = "ocr_fallback"`.
- The `fecha` on the block MUST come from `VisionLLMPort` (handwritten stamp on the first page) — NEVER from SUNAT/electronic date (EXT-017, REC-C01).

**Tests** (update `tests/unit/application/test_pipeline.py`; add `tests/unit/application/test_block_grouping.py`):
- 3 consecutive guía pages, same section, first has QR → single block with `guia_id` from QR, all 3 pages' lines merged (EXT-S15).
- Page 2 has new QR with different `guia_id` → two blocks (EXT-S16).
- Section boundary separates consecutive guía pages → two blocks (EXT-S17).
- 10 guía pages processed → no `GuiaDeRemision.guia_id` matches `guia_page_\d+` pattern (EXT-S18).
- QR decode returns `None` for a page → OCR fallback path used; `identity_source = "ocr_fallback"`.

**Completable in**: one session (longer — modifies pipeline).

### [x] S1.6 — `ReconciliationService` rev-2 update (`guias[]` inline + UNRESOLVED)

**Spec refs**: REC-C01, REC-C02, REC-C03, REC-C04, REC-C05, REC-C06, REC-S01 (modified), REC-C01–REC-C07.
**Depends on**: S1.1 (updated models with `GuiaContribution`), S1.4 (UNRESOLVED).
**Deliverables** (modify `backend/src/reconciliation/domain/reconciliation.py`):
- `reconcile()` output: `ReconciliationRow.guias` populated inline as `list[GuiaContribution]` per group. `summed_qty` is derived (property or computed field: `sum(g.cantidad for g in guias)`). MUST NOT be an independently stored mutable field.
- Group rows by `(registro, fecha, material_canonical, unidad)` using the `guia_id` from each block as the contribution identifier.
- `GuiaContribution.unidad` MUST match the group's unit (units summed independently; contribution must carry its unit).
- `ReconciliationResult` structure: `rows: list[ReconciliationRow]`, `unresolved_guias: list[GuiaDeRemision]`.
- `apply_reassignment(guias, guia_id, new_registro, new_fecha)`: identify by `guia_id` (serie-numero), NOT by source page index alone (REC-C03).
- PROHIBIT: any code path that lets `summed_qty` be written directly via API field `"fecha"` (remove the broken edit path, REC-C04).

**Tests** (update `tests/unit/domain/test_reconciliation.py`):
- **CRITICAL — §F fixture fix**: all test scenarios that use `"4252"` as `registro` MUST be replaced with realistic registro numbers (e.g., `"232"`, `"231"`, `"233"`). This is a pre-condition for EXT-018/REC-C07 regression guards to be meaningful.
- MATCH scenario with `guias[]` populated (REC-S01 modified): `registro="232"`, two `GuiaContribution` entries, `summed_qty` derived correctly.
- `summed_qty` equals sum of `guias[*].cantidad` (property invariant).
- Reassign by `guia_id`: removes from source group, adds to target, recomputes both (REC-C03, REC-C06).
- Unresolved guías (`registro=None`) → appear in `unresolved_guias`, NOT in `rows` (REC-C05, REC-C06).
- No `ReconciliationRow` with `registro="4252"` produced by the reconciler (REC-C07 regression guard).
- Cross-unit guard: `GuiaContribution.unidad` in group `"KG"` never added to group `"TN"`.

**Completable in**: one session.

### [x] S1.7 — `ReviewService` + API surface rev-2 update

**Spec refs**: REC-C04, REC-C06, REV-C02, REV-C03, design §D, design §F (API additions).
**Depends on**: S1.6 (updated reconciliation), 4.2 (existing FastAPI routes).
**Deliverables**:

`backend/src/reconciliation/application/review_service.py` (update):
- `apply_guia_line_edit(guia_id: str, line_index: int | None, material_canonical: str | None, new_cantidad: Decimal) -> list[ReconciliationRow]`: updates the `GuiaContribution.cantidad` for the specified line, recomputes `summed_qty` (derived), recomputes MATCH/MISMATCH, writes audit entry `{action_type="guia_line_edit", guia_id, old_value, new_value}`.
- PROHIBIT: any route that PATCHes `summed_qty` as a direct editable field — if such a route exists, return `422 Unprocessable Entity` (REC-C04, REC-C05 scenario).
- `apply_reassignment` updated to accept `guia_id` (str, `serie-numero`) as primary identifier (REC-C03).

`backend/src/reconciliation/infrastructure/api/schemas.py` (update):
- `GuiaContributionResponse`: `guia_id`, `source_pages`, `cantidad`, `unidad`, `confidence`, `identity_source`.
- `ReconciliationRowResponse`: add `guias: list[GuiaContributionResponse]` (inline).
- `GuiaLineEditRequest`: `{ line_index: int | None, material_canonical: str | None, cantidad: float }`.

`backend/src/reconciliation/infrastructure/api/routes.py` (update):
- Add `PATCH /runs/{run_id}/guias/{guia_id}/lines`: `GuiaLineEditRequest` → `review_service.apply_guia_line_edit` → returns `list[ReconciliationRowResponse]` for affected groups.
- Validate: `cantidad >= 0`, else 422. `guia_id` not found → 404. Idempotent.
- Update `POST /runs/{id}/reassign` to accept `guia_id` in request body (takes precedence over any `source_page`).
- `PATCH /runs/{id}/rows/{row_id}` targeting `field="summed_qty"` or `field="fecha"` (the broken path) → return 422.

**Tests** (update `tests/integration/test_api.py`; add unit tests for review_service):
- `PATCH /runs/{id}/guias/{guia_id}/lines` → 200, affected rows returned with updated `summed_qty`; audit trail updated (REC-C04 scenario).
- Same request twice (idempotent) → same result.
- `cantidad = -1` → 422.
- Unknown `guia_id` → 404.
- `PATCH /runs/{id}/rows/{row_id}` with `field="summed_qty"` → 422 (REC-C05 scenario).
- `POST /runs/{id}/reassign` with `guia_id` → both groups updated (REC-C03 scenario).
- `ReconciliationRowResponse` contains `guias[]` inline (REC-C02 scenario).

**Completable in**: one session.

### [x] S1.8 — Thumbnail backend endpoint

**Spec refs**: REV-005, design §6.
**Depends on**: 4.2 (existing FastAPI routes), 2.2 (RunContext, pages_dir).
**Deliverables** (`backend/src/reconciliation/infrastructure/api/routes.py`):
- Implement `GET /runs/{run_id}/pages/{page}/thumbnail`: reads the deskewed page render PNG from `run_dir/pages/{page:04d}.png`; returns `FileResponse`. 404 when page file does not exist (run not yet processed or page index out of range).
- No new dependencies needed — file is already written to `pages_dir` by the pipeline.

**Tests** (update `tests/integration/test_api.py`):
- Fixture run with a rendered page file → `GET /runs/{id}/pages/0/thumbnail` returns 200 + PNG content-type.
- Non-existent page → 404.

**Parallelism**: independent of S1.1–S1.7; can be implemented in the same session as any other S1 task.
**Completable in**: one session (small — ~50 lines).

### [x] S1.9 — Real-data e2e assertions for rev-2 (backend)

**Spec refs**: EXT-S13, EXT-S14, EXT-S15, EXT-S16, EXT-S17, EXT-S18, EXT-S19, EXT-S20, REC-S01 (modified), REC-C03, REC-C04, REC-C06.
**Depends on**: S1.1–S1.8 complete (all backend hotfix tasks done).
**Deliverables** (update `tests/integration/test_pipeline_e2e.py` or add `test_pipeline_e2e_rev2.py`):
- **QR identity test**: run pipeline on the real PDF; assert at least one `GuiaDeRemision.identity_source == "qr"` and `guia_id` matches `serie-numero` pattern (not `guia_page_\d+`).
- **Block grouping test**: assert no `GuiaDeRemision.guia_id` matches `guia_page_\d+` pattern (EXT-S18 at integration level).
- **UNRESOLVED test**: if any guía page has no section-map match, assert it appears in `unresolved_guias` with `registro=None` or `"UNRESOLVED:*"` (EXT-S19, REC-C06).
- **Section-ID guard**: assert that for all `GuiaDeRemision` in the output, `is_section_id(guia.registro)` is `False` (EXT-S20, REC-C07).
- **Guía-contribution inline**: assert `ReconciliationRow.guias` is non-empty for MATCH/MISMATCH rows (REC-C02).
- **Line-edit e2e**: use `PATCH /runs/{id}/guias/{guia_id}/lines` on a row with known MISMATCH → assert row becomes MATCH (REC-C04).
- **Thumbnail e2e**: `GET /runs/{id}/pages/0/thumbnail` → 200 with PNG content-type.

> Note: this is the trusted gate. Unit mocks alone are not sufficient (per the hard-won lesson in docs/HANDOFF.md §4: "unit tests passed while the real pipeline was broken"). Real-data e2e tests MUST be run before the slice is declared complete.

**Completable in**: one session.

---

## Slice 2 — Frontend Hotfix (rev-2 delta)

> Sequential with slice 1: start only after S1.1–S1.8 backend API surface is stable (API contract needed). Sub-tasks within slice 2 follow a top-down dependency: types → store → components → fixes.

### [x] S2.1 — Update API types + composables for rev-2 contract

**Spec refs**: REC-C02, REC-C06, REV-C01, REV-C02, REV-C03.
**Depends on**: S1.7 (updated backend schemas).
**Deliverables**:
- `frontend/src/api/types.ts`: add `GuiaContributionResponse { guia_id, source_pages, cantidad, unidad, confidence, identity_source }`. Update `ReconciliationRowDTO` to include `guias: GuiaContributionResponse[]`.
- `frontend/src/composables/useReconciliationApi.ts`:
  - Add `useGuiaLineEdit(runId)` mutation → `PATCH /runs/{id}/guias/{guia_id}/lines`.
  - Update `useReassignGuia(runId)` mutation to send `guia_id` in body (not `row_id`).
  - Remove or guard any composable that sends `summed_qty` or `fecha` as an editable field.

**Tests** (`frontend/src/__tests__/api/client.test.ts` updated):
- `useGuiaLineEdit` mutation calls `PATCH /runs/{id}/guias/${guia_id}/lines` with correct body.
- `useReassignGuia` sends `guia_id`, not `row_id`.

**Completable in**: one session (small).

### [x] S2.2 — `GuiaDrillDown` component (REV-C01)

**Spec refs**: REV-C01, REV-C01 scenario, REC-C02.
**Depends on**: S2.1 (types updated).
**Deliverables** (`frontend/src/features/review/GuiaDrillDown.vue`):
- Props: `guias: GuiaContributionResponse[]`, `runId: string`.
- Renders an inline sub-table for each `GuiaContribution`: `guia_id`, `source_pages` (comma-separated), `cantidad`, `unidad`, `ConfidenceBadge` applied to `confidence`, `identity_source` indicator ("QR" badge / "OCR fallback" label).
- Each row has an editable `cantidad` cell → triggers `useGuiaLineEdit` mutation on change (REV-C03 path).
- Each row has a "Reassign" button → emits `reassign(guia_id)` event (opens `GuiaReassignDialog` with the correct `guia_id`).
- Data comes from the already-fetched `ReconciliationRowDTO.guias[]` — NO additional API call on expand (REV-C01: "without a separate API call").

**Tests** (`frontend/src/__tests__/features/GuiaDrillDown.test.ts`):
- Renders all `GuiaContributionResponse` fields (REV-C01 scenario).
- `confidence < 0.85` → `ConfidenceBadge` shows amber (uses existing ConfidenceBadge).
- `identity_source = "qr"` → "QR" badge visible; `"ocr_fallback"` → "OCR fallback" shown.
- Edit `cantidad` cell → `useGuiaLineEdit` mutation called with correct `guia_id` + new value.
- "Reassign" button click → `reassign` event emitted with `guia_id` (REV-C02).
- No extra API call on mount (mock server call count assertion).

**Completable in**: one session.

### [x] S2.3 — `ReconciliationRow` drill-down expansion + `summed_qty` read-only fix

**Spec refs**: REV-C01, REV-C03 (summed_qty read-only), REV-C04 scenario.
**Depends on**: S2.2 (`GuiaDrillDown` component).
**Deliverables** (modify `frontend/src/features/review/ReconciliationRow.vue`):
- Add expand/collapse toggle (chevron icon) per row. When expanded, render `<GuiaDrillDown :guias="row.guias" :runId="runId" />` inline below the row.
- `summed_qty` cell MUST be rendered as a read-only display value (plain text / non-editable `<td>`). REMOVE the `<input>` or any editable control for this cell. This fixes CRITICAL-2 (the `field:'fecha'` corruption bug).
- On `GuiaDrillDown`'s `reassign(guia_id)` event: emit `openReassign({ guia_id })` to parent.
- On `GuiaDrillDown`'s cantidad edit mutation success: emit `rowUpdated` so the parent refreshes its `summed_qty` display.

**Tests** (update `frontend/src/__tests__/features/ReconciliationRow.test.ts`):
- Chevron click → drill-down visible; click again → hidden.
- `summed_qty` cell has no `<input>` element (REV-C04 scenario).
- `GuiaDrillDown` `reassign` event propagated as `openReassign` with `guia_id`.
- Drill-down renders without extra API call.

**Completable in**: one session.

### [x] S2.4 — `GuiaReassignDialog` update: reassign by `guia_id`

**Spec refs**: REV-C02, REV-C02 scenario, REC-C03.
**Depends on**: S2.3 (parent now emits `openReassign({ guia_id })` instead of `{ row_id }`).
**Deliverables** (modify `frontend/src/features/review/GuiaReassignDialog.vue`):
- Change props: accept `guiaId: string` (the `serie-numero` identifier) instead of / in addition to `rowId`. Remove the `rowId`-as-proxy path (CRITICAL-1 fix).
- Submit sends `{ guia_id: props.guiaId, new_registro, new_fecha }` to `POST /runs/{id}/reassign`.
- Display `guiaId` in the dialog header for the engineer's context.
- On success: emit `reassigned` event → parent invalidates rows query → both source and target rows refresh.

**Tests** (update `frontend/src/__tests__/features/GuiaReassignDialog.test.ts`):
- Dialog renders with `guia_id` displayed in header.
- Submit sends `guia_id` (not `row_id`) in mutation payload (REV-C02 scenario).
- On success, `reassigned` event emitted.

**Completable in**: one session (small — surgical change).

### [x] S2.5 — A11y + visual fixes (carry-forward from frontend review)

**Spec refs**: REV-001, REV-004 (UNCLASSIFIED badge neutralization).
**Depends on**: S2.3 (ReconciliationRow stable after summed_qty fix).
**Deliverables** (surgical fixes across multiple components):
- `ReviewGrid.vue`: bind `:aria-rowcount="filteredRows.length"` on the `<table>` element (currently missing or static).
- `SourcePages.vue`: replace `new Image()` probe with a direct `<img :src="thumbnailUrl">` pointing to the API base URL (`/api/runs/{id}/pages/{page}/thumbnail`). This works now that the thumbnail endpoint exists (S1.8). Keep the graceful `onerror` degradation.
- `ReviewGrid.vue` / `ReconciliationRow.vue`: status column MUST remain visible at 768px viewport width (add explicit `min-width` or adjust responsive CSS so the column is not hidden or collapsed at tablet width).
- `ReconciliationRow.vue`: UNCLASSIFIED status badge MUST use the neutral token (`--status-unclassified: #a3a3a3`), NOT the green MATCH token. Remove any code that assigns a green class to UNCLASSIFIED rows.
- Localize status labels: `"MISMATCH"` → `"Diferencia"`, `"MATCH"` → `"Conforme"` (or whichever Spanish terms are shown to the engineer). Update all components and tests that assert on these string labels.

**Tests** (update existing test files):
- `ReviewGrid.test.ts`: assert `aria-rowcount` attribute bound to filtered row count.
- `ReconciliationRow.test.ts`: UNCLASSIFIED badge does NOT have the MATCH CSS class; badge uses neutral color token.
- `ReconciliationRow.test.ts` / `ReviewGrid.test.ts`: status label strings updated to Spanish (localized assertions).
- `SourcePages.test.ts`: thumbnail `<img>` src uses the API base URL pattern; `onerror` degradation still tested.

**Completable in**: one session.

### [x] S2.6 — Unresolved guías bucket (REV-C04)

**Spec refs**: REV-C04, REV-C04 scenario (unresolved guías section), REC-C05.
**Depends on**: S2.1 (API types; `unresolved_guias` must be surfaced in the API response).
**Note**: requires backend to surface `unresolved_guias` in `GET /runs/{id}/rows` response (or a new endpoint). Coordinate with S1.7 API surface.
**Deliverables** (`frontend/src/features/review/UnresolvedGuiasPanel.vue`):
- Props: `unresolvedGuias: GuiaDeRemision[]` (or matching DTO type).
- Renders a collapsible panel listing unresolved guías: `guia_id` (or page range), `identity_source`, `source_pages`.
- Each entry has an "Assign to registro" control that opens `GuiaReassignDialog` with the `guia_id`.
- Unresolved guías MUST NOT appear as rows in the main reconciliation grid.
- Integrated into `ReviewPage.vue` above or alongside the main grid.

**Tests** (`frontend/src/__tests__/features/UnresolvedGuiasPanel.test.ts`):
- 2 unresolved guías → both visible in panel; neither appears in main grid (REV-C04 scenario, REV-C05 scenario).
- "Assign to registro" button → `GuiaReassignDialog` opens with correct `guia_id`.

**Completable in**: one session.

### [x] S2.7 — Frontend smoke + integration test update

**Spec refs**: REV-C01–REV-C04, REV-C04 scenario.
**Depends on**: S2.2–S2.6 complete.
**Deliverables** (update `frontend/src/__tests__/features/smoke.test.ts` or add `smoke_rev2.test.ts`):
- Mount `ReviewPage` with mocked TanStack Query: 2 MISMATCH rows (each with `guias[]` populated), 1 unresolved guía.
- Assert: drill-down expand shows `GuiaDrillDown` for each MISMATCH row.
- Assert: `summed_qty` cell has no `<input>`.
- Assert: "Reassign" button in drill-down emits correct `guia_id`.
- Assert: `UnresolvedGuiasPanel` visible with 1 entry; entry NOT in main grid.
- Assert: `aria-rowcount` attribute present on main grid table.
- Assert: UNCLASSIFIED rows use neutral badge (not green).

**Completable in**: one session (small).

---

## Phase 6 — End-to-End Integration Tests

> Sequential, depends on all prior phases AND slice-1/slice-2 complete.

### [x] (Greenfield) — Hotfix-E2E (9 real-PDF integration tests, Phases 0–5)

Completed 2026-05-31 as part of `Hotfix-E2E` apply slice. 455 backend tests passing. Validated real PDF pipeline including 5 bug fixes uncovered during e2e.

### [x] 6.1 — Backend E2E: full pipeline happy path (greenfield baseline)

**Spec refs**: INJ-S01 through INJ-S05, EXT-S01 through EXT-S12, REC-S01 through REC-S08, EXP-S01 through EXP-S06.
**Depends on**: Phase 4.2 complete.
**Note**: The 4-page programmatic fixture test may already exist from earlier; verify and expand if needed.
**Deliverables** (`tests/integration/test_e2e.py`):
- Happy path: POST /runs → poll status=review → GET rows → PATCH edit → POST reassign → POST export → assert xlsx 10 columns.
- Abort/resume: pipeline aborted after render; restart; cached renders reused.
- Vision cap: `max_vision_calls=1`, 2 guía pages → `VisionCapExceededError` at call 2; partial results preserved.

**Completable in**: one session.

### [x] 6.2 — Backend E2E: error paths

**Spec refs**: INJ-S03, EXT-S04, EXT-S08, EXT-S08b, EXT-S09, EXT-S11, REC-S04, REC-S05.
**Depends on**: 6.1.
**Deliverables** (add to `test_e2e.py` or `test_e2e_errors.py`):
- Corrupt PDF → structured error, no output dir.
- Unclassified page → surfaces in rows with `kind=UNCLASSIFIED`.
- OCR confidence < 0.85 → row flagged `requires_review=True`.
- Vision null date → row flagged `requires_review=True`.
- DECLARED_MISSING: guía with no declared counterpart.
- GUIA_MISSING: declared material with no guía.

**Completable in**: one session.

### [x] 6.3 — Frontend Vitest smoke (greenfield baseline)

**Spec refs**: REV-S01 through REV-S08b.
**Depends on**: Phase 5.5.
**Deliverables** (`frontend/src/__tests__/smoke.test.ts`):
- Mount `ReviewPage` with mocked TanStack Query (3 MISMATCH, 1 DECLARED_MISSING).
- Assert MISMATCH badges, DECLARED_MISSING visible, edit flow, reassign flow.

**Completable in**: one session.

---

## Phase 7 — Config, Hardening, and Local-Run Polish

> Can start once Phase 4 + Phase 5 are functionally complete. Tasks are independent.

### [x] 7.1 — config.yaml + .env.example

**Spec refs**: EXT-006, EXT-008, design §3.
**Depends on**: 2.1.
**Deliverables**:
- `backend/config.yaml` finalized (all provider sub-configs, deskew, confidence, output_dir).
- `backend/.env.example`: `ANTHROPIC_API_KEY=`, `OPENAI_API_KEY=`, `VISION_PROVIDER=anthropic`, `MAX_VISION_CALLS=600`.
- `backend/src/reconciliation/application/config.py` updated to load `.env` via `pydantic-settings` `env_file`.

**Completable in**: one session.

### [x] 7.2 — Cost cap enforcement + audit logging

**Spec refs**: EXT-008, EXT-011, design §4.
**Depends on**: 2.3 (pipeline).
**Deliverables** (`backend/src/reconciliation/application/pipeline.py` updated):
- `vision_calls_budget` counter decremented per call; raises `VisionCapExceededError` (structured: `{calls_made, cap, pages_remaining}`) before submitting the (cap+1)-th call.
- Partial results preserved in sidecar.
- Audit record: `{stage="vision", calls_made, cap_reached: bool}`.

**Tests** (`tests/unit/application/test_pipeline_cap.py`): error at cap+1; partial results accessible; audit record present.

**Completable in**: one session.

### [x] 7.3 — Flagging surface completeness

**Spec refs**: REV-004, INJ-007 (orientation_fallback_failed), INJ-S04, INJ-S05, EXT-S08, EXT-S08b.
**Depends on**: 1.1 (models), 2.3 (pipeline), 4.2 (API).
**Deliverables**:
- `MaterialLine` + `PageClassification` + `ReconciliationRow` models audited for all required flag fields: `requires_review`, `orientation_fallback_failed`, `ocr_empty_after_deskew`, `orientation_low_confidence`.
- Pipeline sets each flag at the correct stage.
- `ReconciliationRowDTO` exposes flags.
- `ReconciliationRow.vue` renders all flag types per REV-004 (icon+label, not color-only).

**Completable in**: one session.

### [x] 7.4 — Local dev run script

**Spec refs**: design migration/rollout (local-first).
**Depends on**: Phase 4.2, Phase 5.5.
**Deliverables**:
- `backend/Makefile` `run` target: `uvicorn reconciliation.infrastructure.api.main:app --reload --port 8000`.
- `frontend/package.json` `dev` script: `vite --port 5173`.
- Repo root `Makefile` `dev` target: runs both concurrently.
- Smoke-test: `make dev` → backend answers health check, frontend renders upload panel.

**Completable in**: one session.

---

## Task Dependency Summary

```
Phase 0 (scaffold) → Phase 1 (domain) → Phase 2 (application) → Phase 3 (adapters)
  → Phase 4 (infra) → Phase 5 (frontend) → [DONE]

Rev-2 delta:
  slice-0 (spec-delta) [DONE]
  → slice-1 (backend hotfix):
      S1.1 (domain types) → S1.2 (QR adapter) → S1.5 (block grouping)
      S1.1 → S1.3 (section-ID guard)           ← parallel with S1.2
      S1.1 + S1.3 → S1.4 (UNRESOLVED fix)
      S1.4 + S1.5 → S1.6 (ReconciliationService)
      S1.6 → S1.7 (ReviewService + API)
      S1.8 (thumbnail endpoint) ← parallel with S1.1–S1.7
      S1.1–S1.8 → S1.9 (real-data e2e)
  → slice-2 (frontend hotfix):
      S2.1 (types + composables)
      → S2.2 (GuiaDrillDown)
      → S2.3 (ReconciliationRow expansion + summed_qty fix)
      → S2.4 (GuiaReassignDialog by guia_id)
      S2.3 → S2.5 (a11y + visual fixes)  ← parallel with S2.4
      S2.1 → S2.6 (UnresolvedGuiasPanel) ← parallel with S2.2–S2.5
      S2.2–S2.6 → S2.7 (smoke + integration tests)
  → Phase 6/7 (e2e + hardening):
      6.1 → 6.2 sequential
      6.3 parallel with 6.1/6.2
      7.1 || 7.2 || 7.3 || 7.4 all independent

Verify → Judgment-Day → Archive (after Phase 6/7)
```

Parallelism:
- Within slice-1: S1.2 || S1.3 (both need only S1.1 done)
- Within slice-1: S1.8 || everything else (independent)
- Within slice-2: S2.4 || S2.5 (both need S2.3 done); S2.6 || S2.2–S2.5 (needs only S2.1)
- Phase 7: 7.1 || 7.2 || 7.3 || 7.4

---

## Review Workload Forecast

### Greenfield phases (Phases 0–5) — COMPLETE
All completed across PR-1, PR-2a, PR-2b-1, PR-2b-2, PR-4, Hotfix-E2E, PR-5a, PR-5b.

### Remaining work estimate

| Slice | Scope | Tasks | Est. changed lines | 400-line risk |
|-------|-------|-------|--------------------|---------------|
| Slice-1 Backend | S1.1–S1.9 | 9 tasks | ~650–750 lines (domain: ~150, adapter: ~150, pipeline: ~120, reconciliation: ~100, API: ~120, e2e tests: ~110) | **High** |
| Slice-2 Frontend | S2.1–S2.7 | 7 tasks | ~500–600 lines (new components: ~250, updates: ~150, tests: ~200) | **High** |
| Phase 6/7 | 6.1, 6.2, 6.3, 7.1–7.4 | 7 tasks | ~450–550 lines (e2e: ~200, hardening: ~250) | **Med** |

| Metric | Estimate |
|--------|----------|
| Total remaining changed lines | ~1,600–1,900 |
| 400-line budget risk (slice-1) | **High** |
| 400-line budget risk (slice-2) | **High** |
| 400-line budget risk (phase 6/7) | **Medium** |
| Chained PRs recommended | **Yes** |
| Decision needed before apply | **Yes** |

### Recommended PR chain (stacked-to-main, previously established delivery strategy)

| PR | Slice | Tasks | Scope | Est. lines |
|----|-------|-------|-------|------------|
| PR-6 | slice-1 | S1.1–S1.4 (domain + QR adapter + UNRESOLVED guard) | Domain types, port, QR adapter, section-ID predicate, UNRESOLVED fix | ~350 |
| PR-7 | slice-1 | S1.5–S1.7 + S1.8 (pipeline + service + API) | Block grouping pipeline, ReconciliationService rev-2, ReviewService + routes, thumbnail endpoint | ~350 |
| PR-8 | slice-1 | S1.9 (real-data e2e) | Backend integration/e2e tests for all rev-2 scenarios | ~150 |
| PR-9 | slice-2 | S2.1–S2.4 (types + drill-down + reassign fix) | API types, composables, GuiaDrillDown, ReconciliationRow expansion, GuiaReassignDialog fix | ~350 |
| PR-10 | slice-2 | S2.5–S2.7 (a11y + unresolved + smoke) | A11y fixes, UnresolvedGuiasPanel, smoke tests | ~250 |
| PR-11 | phase 6/7 | 6.1, 6.2, 6.3, 7.1–7.4 | E2E tests + hardening + local dev scripts | ~450 |

> **Note on PR-6/PR-7 split boundary**: S1.1–S1.4 are pure domain layer (no pipeline mutation) and land safely on their own. S1.5 (block grouping) mutates the pipeline and is the highest-risk task — it must be isolated in PR-7 with PR-6 already merged so the diff is clean.

Each PR is independently reviewable. PR-6 (domain types + QR adapter) has zero side effects on the running pipeline and is the safest merge-first candidate.

**`Decision needed before apply: Yes`** — confirm this PR chain or accept `size:exception` for a single-PR delivery before launching sdd-apply.

---

## Rev-3 Slices

> All prior tasks (Phases 0–7, Slice 1, Slice 2) are complete (50/50, [x]). Rev-3 adds 4 new slices
> (R1–R4) driven by real-pipeline findings: hybrid classifier gap, dual-QR miss, year inference, SUNAT
> opt-in, first_page sentinel, and frontend provenance surfaces. All tasks below are unchecked.
>
> Delivery strategy: stacked-to-main (PR-12 → PR-13 → PR-14 → PR-15). Strict TDD NOT active.

### Rev-3 Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines (rev-3 only) | ~1,300–1,550 |
| 400-line budget risk (R1) | **High** |
| 400-line budget risk (R2) | **High** |
| 400-line budget risk (R3) | **High** |
| 400-line budget risk (R4) | **Medium** |
| Chained PRs recommended | **Yes** |
| Suggested split | PR-12 (R1) → PR-13 (R2) → PR-14 (R3) → PR-15 (R4) |
| Delivery strategy | stacked-to-main |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

#### Rev-3 Work Units

| Unit | Goal | PR | Est. lines | Notes |
|------|------|----|------------|-------|
| R1 | Hybrid classifier + decode_identities pre-pass + dual-QR + first_page sentinel | PR-12 | ~400–450 | CRITICAL unblock; real guías now reach OCR/vision; base = main |
| R2 | Vision adequacy + bounded year inference + provenance | PR-13 | ~350–400 | Pure domain fn + pipeline stage; base = PR-12 merged |
| R3 | SUNAT descargaqr opt-in adapter + pipeline stage (off by default) | PR-14 | ~350–400 | Network egress, off by default; base = PR-13 merged |
| R4 | Frontend year_inferred advisory + first_page=None panel safety | PR-15 | ~200–250 | Vue/vitest only; base = PR-12 merged (can parallel with R2/R3) |

---

### Slice R1 — Hybrid classifier + decode_identities pre-pass + dual-QR + first_page sentinel (UNBLOCK)

> **PR-12 (base: main). Sequential within R1.** This is the critical unblock: without it, scanned guías
> never enter OCR/vision/block-grouping on real input. R2–R4 depend on R1 merged.
>
> Spec refs: EXT-019 (hybrid classifier), EXT-022 (first_page None), D1 (decode_identities pre-pass +
> PageClassifier hybrid OR-gate), D2 (dual-QR multi-res COLOR decode), D6 (first_page sentinel).

- [x] R1.1 — Add `DecodeOutcome` application-layer dataclass to `application/pipeline.py`
  **Spec/Design**: D1 (Decode location). Fields: `identity: GuiaIdentity | None`, `hashqr_url: str | None`, `decoded: bool`. No domain import — pure application dataclass.
  **Files**: `backend/src/reconciliation/application/pipeline.py`.
  **Dependency**: independent; start immediately.

- [x] R1.2 — Implement `_stage_decode_identities(page_count) → dict[int, DecodeOutcome]` in `ReconciliationPipeline`
  **Spec/Design**: D1, EXT-019 (Condition A must reuse the identity already decoded — no second scan). Renders each page once via `DocumentSourcePort.render_page`, calls `self._identity.decode_identity(image, page_idx)` when adapter wired; stores outcome in page→DecodeOutcome map. Graceful: when `self._identity is None`, returns empty map (classifier falls back to Condition B/C only).
  **Files**: `backend/src/reconciliation/application/pipeline.py`.
  **Dependency**: R1.1 done.

- [x] R1.3 — Add `image_coverage_ratio(idx: int) → float` optional method to `DocumentSourcePort`; implement in `PdfStructureAdapter`
  **Spec/Design**: D1 (`image_dominant` signal). Returns ratio 0.0–1.0 of page area covered by raster images. `image_dominant = ratio >= 0.5` computed in pipeline (not domain). Default 0.0 when not supported (graceful).
  **Files**: `backend/src/reconciliation/domain/ports.py`, `backend/src/reconciliation/adapters/pdf/pdf_structure.py`.
  **Dependency**: independent; parallel with R1.1/R1.2.

- [x] R1.4 — Extend `PageClassifier.classify_page` with `qr_is_guia: bool` and `image_dominant: bool`; implement hybrid OR-gate
  **Spec/Design**: D1 §3, EXT-019. Eval order: declared/protocolo title first → Condition A (`qr_is_guia`) → Condition C (digital/ocr title) → Condition B (body < 200 AND Forma-header AND `image_dominant`). `title_matched="QR_IDENTITY"` / `"FORMA_HEADER_HEURISTIC"`. Guard: Condition B MUST NOT fire when body >= 200 chars (EXT-S25).
  **Files**: `backend/src/reconciliation/domain/classifier.py`.
  **Dependency**: independent; parallel with R1.1–R1.3.

- [x] R1.5 — Wire `_stage_decode_identities` into pipeline before `_stage_classify`; pass booleans to classifier; reuse cached decode map in `assemble_blocks`
  **Spec/Design**: D1 — pipeline orchestrates pre-pass, computes `qr_is_guia` + `image_dominant` per page from cached map, feeds into `classify_page`. `assemble_blocks` reads SAME cached map (no re-scan). Render cache: reuse rendered bytes from decode_identities in extract_ocr/assemble_blocks.
  **Files**: `backend/src/reconciliation/application/pipeline.py`.
  **Dependency**: R1.2 + R1.4 done.

- [x] R1.6 — Upgrade `QrBarcodeExtractionAdapter` to multi-resolution COLOR decode
  **Spec/Design**: D2. Keep single `decode_identity(image)` port signature. Adapter internally produces 200-dpi-equivalent and 400-dpi-equivalent scaled variants; decodes each in COLOR (drop `convert("L")`). `_decode_union` returns BOTH compact identity AND `hashqr_url` from URL-variant QR. Propagation: `assemble_blocks` takes first non-null `hashqr_url` across block pages.
  **Files**: `backend/src/reconciliation/adapters/identity/qr_barcode.py`.
  **Dependency**: independent; parallel with R1.1–R1.5.

- [x] R1.7 — Change `GuiaDeRemision.first_page` from `int` (default 0) to `int | None` (default None)
  **Spec/Design**: D6, EXT-022. Fix `UnresolvedGuiaResponse` fallback: `!= 0` idiom → `is not None`. `_GuiaBlock`/`_build_guia_from_block` set concrete index (happy path unaffected). Document semantic shift in `docs/DECISIONS.md`.
  **Files**: `backend/src/reconciliation/domain/models.py`, `backend/src/reconciliation/application/pipeline.py` (UnresolvedGuiaResponse fallback), `docs/DECISIONS.md`.
  **Dependency**: independent; parallel with all above.

- [x] R1.8 — Tests for R1.1–R1.7
  **Spec refs**: EXT-S23, EXT-S24, EXT-S25, EXT-S29, D2.
  **Deliverables**:
  - `tests/unit/domain/test_classifier.py` — `qr_is_guia=True` → GUIA Condition A (EXT-S23); `image_dominant=True` + short body → GUIA Condition B (EXT-S24); 1200-char body → never GUIA via Condition B (EXT-S25).
  - `tests/unit/adapters/test_qr_barcode.py` — COLOR path: URL QR decoded only in COLOR (not grayscale) → `hashqr_url` populated (D2 regression).
  - `tests/unit/domain/test_models.py` — `first_page=0` valid (not None); `is not None` idiom guard (EXT-S29).
  - `tests/unit/application/test_pipeline.py` — `_stage_decode_identities` empty when `self._identity is None`; wiring: passing QR → `qr_is_guia=True` fed to classifier.
  **Files**: `tests/unit/domain/test_classifier.py`, `tests/unit/adapters/test_qr_barcode.py`, `tests/unit/domain/test_models.py`, `tests/unit/application/test_pipeline.py`.
  **Dependency**: R1.1–R1.7 done.

- [x] R1.9 — Real-data e2e assertion for R1 (pages 0–45, registros 230/231/232)
  **Spec refs**: EXT-019, EXT-022, D1, D2.
  **Deliverables** (`tests/integration/test_pipeline_e2e_rev3.py`):
  - Assert registros 230/231/232 produce non-empty `guias` contributions (status != `GUIA_MISSING`) — the critical unblock verification.
  - Assert at least one `GuiaDeRemision.identity_source == "qr"` via COLOR decode.
  - Assert no `first_page` sentinel misuse on real output.
  **Files**: `tests/integration/test_pipeline_e2e_rev3.py`.
  **Dependency**: R1.8 done. Run against real PDF.

---

### Slice R2 — Vision adequacy + bounded year inference + provenance

> **PR-13 (base: PR-12 merged to main). R2.1, R2.2, R2.3 are parallel.**
>
> Spec refs: EXT-020 (stamp-crop adequacy), EXT-021 (bounded year inference), REC-C07 (year_inferred
> propagation), D4 (stamp-crop), D5 (pure domain fn + normalize stage), scenarios EXT-S26–EXT-S28,
> REC-C08, REC-C09.

- [x] R2.1 — Stamp-crop logic in `_stage_extract_vision`; add `vision.stamp_crop` config
  **Spec/Design**: D4, EXT-020. Option A: crop lower-right quadrant (`x∈[0.5,1.0], y∈[0.55,1.0]`) at render DPI from `vision.stamp_crop` config. Fallback to Option B: `render_page(idx, dpi=300)` when crop yields empty/too-small image. Port `read_handwritten_date(image)` unchanged.
  **Files**: `backend/src/reconciliation/application/pipeline.py`, `backend/src/reconciliation/application/config.py`.
  **Dependency**: R1 done (pipeline structure stable).

- [x] R2.2 — Implement `domain/date_inference.py`: pure `infer_reception_year(day, month, lower, upper) → tuple[date | None, bool]`
  **Spec/Design**: D5, EXT-021. Rule: Y where `lower <= date(Y,MM,DD) <= upper`; one → use; multiple → most recent; none → `(None, False)`. No IO. Pure stdlib `datetime`.
  **Files**: `backend/src/reconciliation/domain/date_inference.py`.
  **Dependency**: independent; parallel with R2.1.

- [x] R2.3 — Add `year_inferred: bool = False` to `VisionResult` and `GuiaContribution`; add `any_year_inferred` computed property to `ReconciliationRow`
  **Spec/Design**: D5, REC-C07. `any_year_inferred = any(g.year_inferred for g in guias)`. Default `False` = backward compat.
  **Files**: `backend/src/reconciliation/domain/models.py`.
  **Dependency**: independent; parallel with R2.1/R2.2.

- [x] R2.4 — Implement `_stage_normalize_dates` pipeline stage (after vision, before material normalization)
  **Spec/Design**: D5, EXT-021. For each guía with day-month from vision and absent/garbled year: call `infer_reception_year`; lower bound = SUNAT `fecha_entrega` else OCR-printed GRE date else omitted; upper = PDF doc date else run date. Set `guia.fecha`; set `year_inferred=True` on `VisionResult`; propagate to `GuiaContribution.year_inferred`. `requires_review` when no valid year.
  **Files**: `backend/src/reconciliation/application/pipeline.py`.
  **Dependency**: R2.2 + R2.3 done; R2.1 done.

- [x] R2.5 — Update `ReconciliationService.reconcile` to propagate `year_inferred` and compute `any_year_inferred`
  **Spec/Design**: REC-C07, REC-C08, REC-C09. No MATCH/MISMATCH logic change.
  **Files**: `backend/src/reconciliation/domain/reconciliation.py`.
  **Dependency**: R2.3 done.

- [x] R2.6 — Surface `year_inferred` / `any_year_inferred` in API schemas and export audit
  **Spec/Design**: REC-C07, D5. Add fields to `schemas.py`; include `any_year_inferred` in xlsx audit sheet.
  **Files**: `backend/src/reconciliation/infrastructure/api/schemas.py`, `backend/src/reconciliation/adapters/report/excel.py`.
  **Dependency**: R2.3 + R2.5 done.

- [x] R2.7 — Tests for R2.1–R2.6
  **Spec refs**: EXT-S26, EXT-S27, EXT-S28, REC-C08, REC-C09.
  **Deliverables**:
  - `tests/unit/domain/test_date_inference.py` — lower+upper (EXT-S27); upper-only (EXT-S28); no valid year → `(None, False)`; multiple valid → most recent.
  - `tests/unit/domain/test_models.py` — `any_year_inferred` True/False logic (REC-C08/C09).
  - `tests/unit/application/test_pipeline.py` — `_stage_normalize_dates`: DD=28/MM=05 + lower=2026-05-20 + upper=2026-06-01 → fecha=2026-05-28, `year_inferred=True`.
  - `tests/unit/domain/test_reconciliation.py` — propagation from guía to `GuiaContribution`; `any_year_inferred` computed.
  **Files**: `tests/unit/domain/test_date_inference.py`, `tests/unit/domain/test_models.py`, `tests/unit/application/test_pipeline.py`, `tests/unit/domain/test_reconciliation.py`.
  **Dependency**: R2.2–R2.5 done.

- [x] R2.8 — Real-data e2e assertion for R2
  **Spec refs**: EXT-020, EXT-021, REC-C07.
  **Deliverables** (append to `tests/integration/test_pipeline_e2e_rev3.py`):
  - Assert at least one guía has non-null `fecha` after vision (EXT-S26 gate).
  - Assert `any_year_inferred` field present in `GET /runs/{id}/rows` JSON response.
  **Files**: `tests/integration/test_pipeline_e2e_rev3.py`.
  **Dependency**: R2.7 done.

---

### Slice R3 — SUNAT descargaqr opt-in adapter (off by default)

> **PR-14 (base: PR-13 merged to main). R3.1–R3.3 are parallel. R3.1–R3.4 can start in parallel with
> R2 domain/adapter work (no pipeline coupling until R3.5).**
>
> Spec refs: EXT-023, D3, EXT-S30, EXT-S31, EXT-S32.

- [ ] R3.1 — Promote `OfficialGre` to a pure domain Pydantic model
  **Spec/Design**: D3, EXT-023 (Hexagonal). Fields: `guia_id: str`, `ruc_emisor: str`, `ruc_receptor: str`, `fecha_emision: date | None`, `fecha_entrega: date | None`, `lines: list[MaterialLine]`. No IO.
  **Files**: `backend/src/reconciliation/domain/models.py`.
  **Dependency**: independent; start immediately.

- [ ] R3.2 — Implement `SunatDescargaqrAdapter` in `adapters/sunat/descargaqr.py`
  **Spec/Design**: D3, EXT-023. Plain HTTP GET on `hashqr_url` (no OAuth). Parse GRE PDF with PyMuPDF `get_text()` → `OfficialGre`. Failure → `None` (never raise). Cache PDF at `<run_dir>/sunat/{guia_id}.pdf`; reuse on re-run. Lazy-import `httpx`/`urllib` AND PyMuPDF inside `fetch()`.
  **Files**: `backend/src/reconciliation/adapters/sunat/descargaqr.py`, `backend/src/reconciliation/adapters/sunat/__init__.py`.
  **Dependency**: R3.1 done.

- [ ] R3.3 — Add `sunat` config block to `AppConfig` and `config.yaml`; document as air-gap exception in `docs/DECISIONS.md`
  **Spec/Design**: D3. `sunat: { enabled: bool = False, timeout_s: float = 10.0, cache: bool = True }`. `config.yaml` ships `enabled: false`.
  **Files**: `backend/src/reconciliation/application/config.py`, `backend/config.yaml`, `docs/DECISIONS.md`.
  **Dependency**: independent; parallel with R3.1/R3.2.

- [ ] R3.4 — Wire `SunatDescargaqrAdapter` in `container.py` behind `config.sunat.enabled`
  **Spec/Design**: D3 — `build_pipeline` passes adapter only when `enabled is True`, else `None`.
  **Files**: `backend/src/reconciliation/infrastructure/container.py`.
  **Dependency**: R3.2 + R3.3 done.

- [ ] R3.5 — Implement `_stage_sunat_fetch` in pipeline (after `assemble_blocks`, before quantity assignment)
  **Spec/Design**: D3. For each block with non-null `gre_hashqr_url` and `enabled`: fetch → on success cache PDF + replace OCR lines with SUNAT lines + record `fecha_entrega` as year-inference lower bound; on failure log + leave OCR intact. No-op when `self._sunat is None`.
  **Files**: `backend/src/reconciliation/application/pipeline.py`.
  **Dependency**: R3.2 + R3.4 done; R1 pipeline structure stable.

- [ ] R3.6 — Enforce SUNAT > OCR quantity precedence in reconciliation; add `extraction_method: Literal["ocr","sunat_gre","digital_text"]` to `MaterialLine`
  **Spec/Design**: D3 precedence, EXT-023. SUNAT lines replace OCR for the same block. Grouping `fecha` ALWAYS vision (invariant guard). `MaterialLine.extraction_method` tracks provenance.
  **Files**: `backend/src/reconciliation/domain/models.py`, `backend/src/reconciliation/domain/reconciliation.py`.
  **Dependency**: R3.1 + R3.5 done.

- [ ] R3.7 — Tests for R3.1–R3.6 (network tests gated on `sunat.enabled=true`)
  **Spec refs**: EXT-S30, EXT-S31, EXT-S32.
  **Deliverables**:
  - `tests/unit/adapters/test_sunat_descargaqr.py` — mocked HTTP valid PDF → `OfficialGre`; timeout → `None`; non-200 → `None`; lazy-import guard (no `ImportError` at module load).
  - `tests/unit/application/test_pipeline.py` — `_stage_sunat_fetch` no-op when port `None` (EXT-S31); success → block lines replaced (EXT-S30).
  - `tests/unit/domain/test_reconciliation.py` — SUNAT `extraction_method="sunat_gre"`; grouping fecha stays vision.
  - Network tests: `@pytest.mark.skipif(not sunat_enabled, ...)`.
  **Files**: `tests/unit/adapters/test_sunat_descargaqr.py`, `tests/unit/application/test_pipeline.py`, `tests/unit/domain/test_reconciliation.py`.
  **Dependency**: R3.2 + R3.5 + R3.6 done.

- [ ] R3.8 — Real-data e2e for R3 (skippable when `sunat.enabled=false`)
  **Spec refs**: EXT-S30, EXT-S31.
  **Deliverables** (append to `tests/integration/test_pipeline_e2e_rev3.py`):
  - `@pytest.mark.skipif(not config.sunat.enabled, ...)` — when enabled: assert `OfficialGre` returned for ≥1 block; assert grouping `fecha` is vision handwritten date (not SUNAT date).
  - Always-on: with `enabled=false`, zero HTTP calls (spy assertion).
  **Files**: `tests/integration/test_pipeline_e2e_rev3.py`.
  **Dependency**: R3.7 done.

---

### Slice R4 — Frontend: year_inferred advisory + first_page=None panel safety

> **PR-15 (base: PR-12 merged; can run in parallel with R2/R3). R4.1 → R4.2 || R4.3 → R4.4.**
>
> Spec refs: REV-C05 (year_inferred advisory), REV-C06 (first_page=None safety), REV-C06–REV-C08
> scenarios.

- [ ] R4.1 — Update `frontend/src/api/types.ts`: add `year_inferred: boolean` to `GuiaContributionResponse`, `any_year_inferred: boolean` to `ReconciliationRowDTO`, type `first_page: number | null` on unresolved guía DTO
  **Spec/Design**: REC-C07, REV-C05. Default `false` for backward compat.
  **Files**: `frontend/src/api/types.ts`.
  **Dependency**: independent; start immediately after PR-12 merged.

- [ ] R4.2 — Implement `YearInferredBadge.vue`; integrate into `GuiaDrillDown.vue` and `ReconciliationRow.vue`
  **Spec/Design**: REV-C05, scenario REV-C06. Yellow badge on fecha cell when `year_inferred === true`. Expand/tooltip: day-month (vision), inferred year, inference bounds. Confirm/override via existing date-edit path; audit `action_type="year_inferred_confirmed"/"year_inferred_overridden"`. `any_year_inferred=true` on aggregate row → advisory indicator (not red MISMATCH badge).
  **Files**: `frontend/src/features/review/YearInferredBadge.vue` (new), `frontend/src/features/review/GuiaDrillDown.vue`, `frontend/src/features/review/ReconciliationRow.vue`.
  **Dependency**: R4.1 done.

- [ ] R4.3 — Fix `UnresolvedGuiasPanel.vue` for `first_page: null` and `first_page: 0` (REV-C06)
  **Spec/Design**: REV-C06, scenarios REV-C07, REV-C08. `null` → show `source_pages` list or "página desconocida". `0` → show "pág. 1" (1-based), NOT treated as absent. Replace all `!= 0` guards with `!== null`.
  **Files**: `frontend/src/features/review/UnresolvedGuiasPanel.vue`.
  **Dependency**: R4.1 done.

- [ ] R4.4 — Tests for R4.1–R4.3; verify vitest + vue-tsc clean
  **Spec refs**: REV-C06 scenario, REV-C07, REV-C08.
  **Deliverables**:
  - `frontend/src/__tests__/features/YearInferredBadge.test.ts` (new) — badge renders when `year_inferred=true`; absent when `false`; tooltip shows bounds; confirm emits correct `action_type`.
  - `frontend/src/__tests__/features/GuiaDrillDown.test.ts` (update) — `YearInferredBadge` present; `any_year_inferred=true` → advisory on aggregate row (not red).
  - `frontend/src/__tests__/features/UnresolvedGuiasPanel.test.ts` (update) — `first_page=null` + `source_pages=[12,13]` → pages shown, no error (REV-C07); `first_page=0` → "pág. 1", no fallback (REV-C08); `first_page=null` + `source_pages=[]` → "página desconocida".
  **Files**: `frontend/src/__tests__/features/YearInferredBadge.test.ts`, `frontend/src/__tests__/features/GuiaDrillDown.test.ts`, `frontend/src/__tests__/features/UnresolvedGuiasPanel.test.ts`.
  **Dependency**: R4.2 + R4.3 done.

---

### Rev-3 Task Dependency Graph

```
R1 (PR-12) — CRITICAL UNBLOCK — base: main:
  R1.1 → R1.2 → R1.5
  R1.3 ──────── parallel with R1.1/R1.2
  R1.4 ──────── parallel with R1.1/R1.2/R1.3
  R1.6 ──────── parallel with R1.1–R1.5
  R1.7 ──────── parallel with all above
  R1.1–R1.7 → R1.8 → R1.9 (real-data gate)

R2 (PR-13) — base: PR-12 merged:
  R2.1 || R2.2 || R2.3  (all parallel)
  R2.2 + R2.3 → R2.4
  R2.3 → R2.5
  R2.3 + R2.5 → R2.6
  R2.1–R2.6 → R2.7 → R2.8 (real-data gate)

R3 (PR-14) — base: PR-13 merged:
  R3.1 → R3.2
  R3.3 ──────── parallel with R3.1/R3.2
  R3.2 + R3.3 → R3.4
  R3.2 + R3.4 → R3.5
  R3.1 + R3.5 → R3.6
  R3.2–R3.6 → R3.7 → R3.8 (skippable)

R4 (PR-15) — base: PR-12 merged (can parallel with R2/R3):
  R4.1 → R4.2 || R4.3 → R4.4

Cross-slice parallelism:
  R3.1–R3.4 can start while R2 is in-flight (domain/adapter only)
  R4.1–R4.3 can start once PR-12 merged (independent of R2/R3)
```

### Rev-3 Total Count

| Slice | Tasks | PR | Spec Coverage |
|-------|-------|----|---------------|
| R1 | 9 (R1.1–R1.9) | PR-12 | EXT-019, EXT-022, D1, D2, D6 |
| R2 | 8 (R2.1–R2.8) | PR-13 | EXT-020, EXT-021, REC-C07, D4, D5 |
| R3 | 8 (R3.1–R3.8) | PR-14 | EXT-023, D3, EXT-S30/31/32 |
| R4 | 4 (R4.1–R4.4) | PR-15 | REV-C05, REV-C06, REV-C07/08 |
| **Total rev-3** | **29** | PR-12–15 | All rev-3 spec IDs covered |

**Grand total (all phases + rev-2 + rev-3)**: 50 (complete) + 29 (pending) = **79 tasks**.
