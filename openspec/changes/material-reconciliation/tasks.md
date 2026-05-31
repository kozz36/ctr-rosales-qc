# Tasks — material-reconciliation

**Change**: `material-reconciliation` · **Phase**: tasks · **Store**: hybrid · **Date**: 2026-05-31

Greenfield build. Hexagonal architecture (domain / application / adapters / infrastructure). All locked decisions honored: MATCH tolerance EXACT(0), confidence auto-flag 0.85, deskew guía-only + orientation fallback, review persistence per-run sidecar review.json, xlsx 10-column set + summary sheet, provider-agnostic VisionLLMPort. `strict_tdd: false` — tests are included (standard mode).

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
- `backend/README.md` with one-sentence description (excluded from word count per persona scope rules — this is the only doc file created; it is a greenfield project requirement).

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
- `frontend/src/design/tokens.css` with empty variable blocks: `--status-match`, `--status-mismatch`, `--status-flag`, `--confidence-low`, `--confidence-ok`, spacing vars, type scale vars.
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
- `LOW_CONFIDENCE_THRESHOLD: float = 0.85` (module constant, used internally for returning confidence score).

**Tests** (`tests/unit/domain/test_classifier.py`):
- Each title variant returns expected `kind`.
- Missing/empty title → `UNCLASSIFIED`.
- Case-insensitive match.
- Supplier name not used (test with supplier-name-only text → `UNCLASSIFIED`).

**Completable in**: one session.

### [x] 1.3 — MaterialNormalizer

**Spec refs**: REC-001, REC-002, EXT-010.
**Depends on**: 1.1.
**Deliverables** (`backend/src/reconciliation/domain/normalizer.py`):
- `MaterialNormalizer.canonicalize(description: str) -> str`: lowercase → strip → collapse whitespace → normalize unicode (NFC). MUST NOT modify unit. Returns canonical description string.
- No external library deps — stdlib only (`unicodedata`).

**Tests** (`tests/unit/domain/test_normalizer.py`):
- Extra whitespace collapsed.
- Unicode NFC applied.
- Unit string passed alongside separately is unchanged.
- Empty string returns empty string.

**Completable in**: one session.

### [x] 1.4 — ReconciliationService

**Spec refs**: REC-001 through REC-010, REC-S01 through REC-S08.
**Depends on**: 1.1, 1.3.
**Deliverables** (`backend/src/reconciliation/domain/reconciliation.py`):
- `ReconciliationService.reconcile(declared: list[Registro], guias: list[GuiaDeRemision]) -> list[ReconciliationRow]`:
  - Groups `GuiaDeRemision.lines` by `(registro, fecha, material_canonical, unidad)`.
  - Sums `cantidad` per group using `Decimal` arithmetic. NO cross-unit addition.
  - MATCH: `summed_qty == declared_qty` exactly (tolerance = 0, no epsilon).
  - MISMATCH: any `delta != 0`.
  - `declared_qty` missing → `status=DECLARED_MISSING`, `declared_qty=Decimal(0)`.
  - `guia` rows with no declared counterpart → `status=GUIA_MISSING`.
  - `min_confidence` = `min(confidence for line if confidence is not None)` across contributing `MaterialLine`s.
  - PURE: no I/O, no imports outside domain, no framework deps (REC-008).
- `ReconciliationService.apply_reassignment(guias, guia_id, new_registro, new_fecha) -> list[GuiaDeRemision]`: returns updated list (new immutable copy).

**Tests** (`tests/unit/domain/test_reconciliation.py`):
- MATCH: sum equals declared exactly.
- MISMATCH: delta=+10.0 flagged.
- Cross-unit guard: TN and KG groups remain separate.
- DECLARED_MISSING: guía with no declared counterpart.
- GUIA_MISSING: declared with no guía rows → summed=0.
- Reassignment recomputes source and target groups.
- All 320 guía pages appear in output (no silent exclusion).
- Pure: no I/O called (assert no file/network calls possible by design).

**Completable in**: one session.

### [x] 1.5 — Port definitions

**Spec refs**: design §2, EXT-006, EXT-007, EXT-009, EXP-007.
**Depends on**: 1.1.
**Parallel with**: 1.2, 1.3, 1.4.
**Deliverables** (`backend/src/reconciliation/domain/ports.py`):
- `DocumentSourcePort(Protocol)`: `page_count() -> int`, `render_page(idx: int, dpi: int = 200) -> bytes`, `page_text(idx: int) -> str | None`.
- `ExtractionPort(Protocol)`: `extract_declared(text: str) -> list[MaterialLine]`, `extract_printed_table(image: bytes) -> list[MaterialLine]`.
- `VisionLLMPort(Protocol)`: `supports_batch: bool`, `read_handwritten_date(image: bytes, hint: str | None = None) -> VisionResult`, `read_handwritten_date_batch(images: list[bytes]) -> list[VisionResult]`.
- `ReportPort(Protocol)`: `export(rows: list[ReconciliationRow], audit_trail: list[dict], dst: Path, fmt: Literal["xlsx","csv"]) -> Path`.
- All are `typing.Protocol` with `runtime_checkable=True`.

**Tests** (`tests/unit/domain/test_ports.py`): Protocol structural compliance — a minimal stub implementing each protocol passes `isinstance` check.

**Completable in**: one session.

---

## Phase 2 — Application Layer

> 2.1 → 2.2 → 2.3 sequential (pipeline depends on config and run context). 2.4 parallel with 2.1.

### 2.1 — AppConfig (pydantic-settings)

**Spec refs**: design §3, EXT-006, EXT-008, INJ-007.
**Depends on**: Phase 0.
**Deliverables** (`backend/src/reconciliation/application/config.py`):
- `VisionConfig(BaseSettings)`: `provider: Literal["anthropic","openai","ollama"]`, `max_vision_calls: int = 600`, `anthropic: AnthropicProviderConfig`, `openai: OpenAIProviderConfig`, `ollama: OllamaProviderConfig`. Each sub-config: model name, api_key_env, base_url (ollama).
- `DeskewConfig(BaseSettings)`: `scope: Literal["guia_only"] = "guia_only"`.
- `ConfidenceConfig(BaseSettings)`: `auto_flag_below: float = 0.85`.
- `AppConfig(BaseSettings)`: composes `VisionConfig`, `DeskewConfig`, `ConfidenceConfig`, `output_dir: Path`, `max_dpi: int = 200`.
- Reads from `config.yaml` via `model_config = SettingsConfigDict(yaml_file="config.yaml")`.
- `backend/config.yaml` template written with all locked defaults (provider=anthropic, max_vision_calls=600, scope=guia_only, auto_flag_below=0.85, output_dir=./runs).

**Tests** (`tests/unit/application/test_config.py`): default values match locked decisions; env var override works; invalid provider raises `ValidationError`.

**Completable in**: one session.

### 2.2 — RunContext

**Spec refs**: INJ-003, INJ-008, REV-008.
**Depends on**: 2.1.
**Deliverables** (`backend/src/reconciliation/application/run_context.py`):
- `RunContext`: `run_id: str` (UUID4), `run_dir: Path` (output_dir / run_id), `pdf_path: Path`, `pages_dir: Path` (run_dir/pages), `review_sidecar: Path` (run_dir/review.json).
- `create_run(pdf_path, output_dir) -> RunContext`: creates dirs, validates PDF read-only (opens read-mode).
- `load_review(ctx) -> dict`: reads `review.json` if exists, returns `{}` if absent (no crash).
- `save_review(ctx, state: dict) -> None`: atomic write (write to `.tmp`, rename).
- `is_page_cached(ctx, page_idx) -> bool`: checks `pages_dir/{page_idx:04d}.png` exists.

**Tests** (`tests/unit/application/test_run_context.py`): dir creation, missing review.json returns empty dict, atomic save, cache hit/miss.

**Completable in**: one session.

### 2.3 — ReconciliationPipeline

**Spec refs**: design §4, INJ-001 through INJ-009, EXT-001 through EXT-011, REC-001 through REC-010.
**Depends on**: 1.1–1.5, 2.1, 2.2.
**Deliverables** (`backend/src/reconciliation/application/pipeline.py`):
- `ReconciliationPipeline(doc: DocumentSourcePort, extraction: ExtractionPort, vision: VisionLLMPort, classifier: PageClassifier, normalizer: MaterialNormalizer, reconciler: ReconciliationService, config: AppConfig)`.
- `run(ctx: RunContext) -> list[ReconciliationRow]` implements the fixed sequence:
  1. **split**: `doc.page_count()` → emit audit record.
  2. **render**: for each page, skip if `is_page_cached`; call `doc.render_page(idx, dpi=config.max_dpi)` → write PNG to `pages_dir`. Store bytes in memory for downstream.
  3. **classify (initial)**: `doc.page_text(idx)` → `classifier.classify(text, ocr_title=None)` → `PageClassification`. Orientation fallback: if classification confidence < 0.85 or title OCR empty, flag for deskew-before-reclassify.
  4. **deskew**: only pages classified as `GUIA` OR pages in fallback set. Pass image bytes to `DeskewAdapter`. Overwrite rendered bytes for downstream. Re-classify fallback pages; if still not `GUIA`, set `orientation_fallback_failed=True` on page record.
  5. **extract declared**: `declared` and `protocolo` pages → `extraction.extract_declared(text)`. NO OCR.
  6. **extract guía tables (OCR)**: `GUIA` pages → `extraction.extract_printed_table(image)`. Flag rows with confidence < 0.85 as `requires_review=True`.
  7. **extract handwritten dates (vision)**: check `vision_calls_used <= config.max_vision_calls`; if batch: `vision.read_handwritten_date_batch(images)`; else: sequential loop. Flag dates with confidence < 0.85 or `date=None` as `requires_review=True`. Exceeding cap → raise `VisionCapExceededError`.
  8. **normalize**: `normalizer.canonicalize(line.description_raw)` → set `description_canonical`.
  9. **reconcile**: `reconciler.reconcile(declared, guias)` → rows.
  10. **persist sidecar**: `save_review(ctx, initial_state)`.
  11. Return rows.
- Inline docstring mapping each step to spec requirement ID.

**Tests** (`tests/unit/application/test_pipeline.py`): full pipeline with stub adapters (all ports mocked); asserts stage order, deskew called only for GUIA, vision cap enforced, sidecar written.

**Completable in**: one session (one long session).

### 2.4 — ReviewService

**Spec refs**: REV-001 through REV-009, REC-006.
**Depends on**: 1.4, 2.2.
**Parallel with**: 2.3 (no dependency on pipeline).
**Deliverables** (`backend/src/reconciliation/application/review_service.py`):
- `ReviewService(reconciler: ReconciliationService, ctx: RunContext)`.
- `apply_edit(row_id, field, new_value) -> list[ReconciliationRow]`: updates sidecar, re-runs `reconciler.reconcile` for affected group only (REV-006). Records audit entry `{timestamp, action_type="value_edit", field, old_value, new_value, operator="engineer"}`.
- `apply_reassignment(guia_id, new_registro, new_fecha) -> list[ReconciliationRow]`: moves guía, recomputes source and target groups, records audit entry `{action_type="guia_reassign", target=guia_id, old=(registro,fecha), new=(new_registro,new_fecha)}`.
- `get_audit_trail(ctx) -> list[dict]`: reads from sidecar.
- `restore_from_sidecar(ctx) -> dict`: loads `review.json`, returns state dict (original values + edits + reassignments + audit trail). Used on restart (REV-008).

**Tests** (`tests/unit/application/test_review_service.py`): edit recomputes group, original value preserved in audit, reassign recomputes both groups, sidecar state survives round-trip.

**Completable in**: one session.

---

## Phase 3 — Adapters

> 3.1–3.5 are independent and can run in parallel once Phase 1 (ports) is done. 3.6 depends on 3.5.

### [x] 3.1 — PdfStructureAdapter (PyMuPDF)

**Spec refs**: INJ-001 through INJ-009, design §2 `DocumentSourcePort`.
**Depends on**: 1.5.
**Deliverables** (`backend/src/reconciliation/adapters/pdf/pymupdf_source.py`):
- `PdfStructureAdapter(DocumentSourcePort)`:
  - `__init__(pdf_path: Path)`: opens in read-only mode (`fitz.open(str(pdf_path))`). Raises `IngestionError` if not readable.
  - `page_count() -> int`.
  - `render_page(idx: int, dpi: int = 200) -> bytes`: renders to PNG bytes via `page.get_pixmap(dpi=dpi).tobytes("png")`.
  - `page_text(idx: int) -> str | None`: `page.get_text("text")` stripped; `None` if empty or whitespace-only.
- Satisfies INJ-001: opens with `fitz.open(..., filetype="pdf")` — no write access.

**Tests** (`tests/integration/test_pymupdf.py`): use a 2-page test PDF fixture (created in test setup via PyMuPDF); assert `page_count=2`, `render_page` returns PNG bytes, `page_text` returns str or None, source PDF unchanged.

**Completable in**: one session.

### [x] 3.2 — DeskewAdapter (PaddleOCR)

**Spec refs**: INJ-007, design §1 `DeskewAdapter`.
**Depends on**: 1.5.
**Deliverables** (`backend/src/reconciliation/adapters/ocr/paddle_deskew.py`):
- `DeskewAdapter`:
  - `__init__()`: initializes `DocImgOrientationClassification` (PaddleOCR) lazily on first call.
  - `correct_orientation(image_bytes: bytes) -> tuple[bytes, int]`: returns (corrected_png_bytes, detected_angle_degrees). Detects angle → rotates image → returns corrected bytes. Supports 0, 90, 180, 270.
  - `detect_only(image_bytes: bytes) -> int`: returns detected angle without rotating.

**Tests** (`tests/unit/adapters/test_deskew.py`): mock PaddleOCR; assert angle detection calls correct rotation; 0° input returns same bytes conceptually.

**Completable in**: one session.

### [x] 3.3 — PrintedTableAdapter (PaddleOCR)

**Spec refs**: EXT-004, EXT-009, EXT-010, design §2 `ExtractionPort.extract_printed_table`.
**Depends on**: 1.5.
**Deliverables** (`backend/src/reconciliation/adapters/ocr/paddle_table.py`):
- `PrintedTableAdapter(ExtractionPort partial)`:
  - `extract_printed_table(image: bytes) -> list[MaterialLine]`: runs PaddleOCR on image bytes, parses table structure. Each recognized row → `MaterialLine(description_raw, description_canonical="", unidad, cantidad, confidence, source_page)`. Rows with confidence < 0.85 automatically set `requires_review=True` in a side-channel (or use a flag field on MaterialLine). Empty table result → returns `[]`.
  - Raw unit string preserved as extracted; normalization is done by `MaterialNormalizer`.
  - `extract_declared` NOT implemented on this adapter (raises `NotImplementedError` — declared extraction is text-only).
- `MaterialLine` extended with optional `requires_review: bool = False` field (update `domain/models.py` if not already added in 1.1).

**Tests** (`tests/integration/test_paddle_table.py`): run on a small fixture guía page image (PNG); assert non-empty result, `MaterialLine` fields populated, confidence attached.

**Completable in**: one session.

### [x] 3.4 — DigitalTextExtractionAdapter

**Spec refs**: EXT-003, EXT-009, INJ-006.
**Depends on**: 1.5.
**Deliverables** (`backend/src/reconciliation/adapters/pdf/text_extraction.py`):
- `DigitalTextExtractionAdapter(ExtractionPort partial)`:
  - `extract_declared(text: str) -> list[MaterialLine]`: parses digital text of declared/protocolo pages. Heuristic: line-by-line, match pattern `<description> <quantity> <unit>`. Returns `MaterialLine` with `confidence=None` (trusted digital source, per EXT-003).
  - `extract_printed_table` NOT implemented (raises `NotImplementedError`).
- This is a pure text parser — no OCR, no I/O.

**Tests** (`tests/unit/adapters/test_text_extraction.py`): fixture text strings covering single-line and multi-line material lists; assert `MaterialLine` count and field values; `confidence=None`.

**Completable in**: one session.

### [x] 3.5 — Vision adapters + factory

**Spec refs**: EXT-005, EXT-006, EXT-007, EXT-008, EXT-011, design §3.
**Depends on**: 1.5.
**Deliverables**:

`backend/src/reconciliation/adapters/vision/anthropic_vision.py`:
- `AnthropicVisionAdapter(VisionLLMPort)`:
  - `supports_batch = True`.
  - `read_handwritten_date(image: bytes, hint: str | None = None) -> VisionResult`: base64-encodes image, calls `anthropic.Anthropic().messages.create()` with vision message. Parses response JSON `{date, confidence, raw}`.
  - `read_handwritten_date_batch(images: list[bytes]) -> list[VisionResult]`: uses Anthropic Message Batches API.

`backend/src/reconciliation/adapters/vision/openai_compatible.py`:
- `OpenAICompatibleVisionAdapter(VisionLLMPort)`:
  - `supports_batch: bool` (True for OpenAI, False for Ollama — set via config).
  - `read_handwritten_date(image: bytes, hint: str | None = None) -> VisionResult`: OpenAI chat completions with vision. `base_url` swappable (`http://localhost:11434/v1` for Ollama).
  - `read_handwritten_date_batch(images: list[bytes]) -> list[VisionResult]`: OpenAI Batch API when `supports_batch=True`; raises `NotImplementedError` when False (Ollama — pipeline falls back to sequential loop).

`backend/src/reconciliation/adapters/vision/factory.py`:
- `build_vision_adapter(cfg: VisionConfig) -> VisionLLMPort`: Strategy selector. `anthropic` → `AnthropicVisionAdapter`; `openai` → `OpenAICompatibleVisionAdapter(batch=True)`; `ollama` → `OpenAICompatibleVisionAdapter(base_url=ollama_url, batch=False)`.

**Tests** (`tests/unit/adapters/test_vision.py`): mock `anthropic.Anthropic` and `openai.OpenAI`; assert `VisionResult` returned; batch path called when `supports_batch=True`; factory returns correct adapter type per provider config; Ollama `supports_batch=False`.

**Completable in**: one session.

### [x] 3.6 — ExcelReportAdapter

**Spec refs**: EXP-001 through EXP-008, design §1 `ExcelReportAdapter`.
**Depends on**: 1.5, 1.1.
**Deliverables** (`backend/src/reconciliation/adapters/report/xlsx_report.py`):
- `ExcelReportAdapter(ReportPort)`:
  - `export(rows: list[ReconciliationRow], audit_trail: list[dict], dst: Path, fmt: Literal["xlsx","csv"]) -> Path`:
    - **xlsx**: creates workbook with two sheets:
      1. `Reconciliacion`: 10 columns exactly: `Registro | Fecha | Material | Unidad | Declarado | Sumado (guías) | Delta | Estado | Confianza mín | Páginas origen`. Ordered by Registro asc, Fecha asc, Material asc. Corrected value used for Sumado.
      2. `Resumen`: total groups, MATCH count, MISMATCH count, DECLARED_MISSING count, GUIA_MISSING count, run ID, export timestamp.
      3. `Audit Trail`: columns `timestamp | action_type | target | old_value | new_value | operator`.
    - **csv**: writes `*_reconciliation.csv` (10 columns, UTF-8) + `*_audit.csv` (audit trail, UTF-8) both in `dst` directory.
    - Filename includes run_id or timestamp; MUST NOT overwrite source PDF.
    - Uses `openpyxl` for xlsx, stdlib `csv` for csv.
  - Export is idempotent (overwrites previous file on repeat call).

**Tests** (`tests/integration/test_xlsx_report.py`): call `export` with fixture rows; assert xlsx workbook has 3 sheets, exactly 10 data columns in Reconciliacion sheet, Summary sheet has correct counts, Audit Trail sheet present; csv test asserts two files created.

**Completable in**: one session.

---

## Phase 4 — Infrastructure / Wiring

> 4.1 → 4.2 sequential. 4.1 can start once Phase 3 adapters are done.

### [x] 4.1 — Composition root (container.py)

**Spec refs**: design §1 infrastructure, EXT-006, EXP-007.
**Depends on**: 2.1, 2.3, 2.4, 3.1–3.6.
**Deliverables** (`backend/src/reconciliation/infrastructure/container.py`):
- `build_pipeline(cfg: AppConfig) -> tuple[ReconciliationPipeline, ReviewService]`:
  - Creates `PdfStructureAdapter`, `DeskewAdapter`, `PrintedTableAdapter`, `DigitalTextExtractionAdapter`, vision adapter via `build_vision_adapter(cfg.vision)`.
  - Wraps extraction adapters into a unified `CompositeExtractionAdapter(ExtractionPort)` that delegates `extract_declared` to `DigitalTextExtractionAdapter` and `extract_printed_table` to `PrintedTableAdapter` (satisfies EXT-009: callers see one `ExtractionPort`).
  - Creates `PageClassifier`, `MaterialNormalizer`, `ReconciliationService`.
  - Returns `ReconciliationPipeline` and `ReviewService` wired with all deps.
- Application layer imports ports only; `container.py` is the only module that imports concrete adapters.

**Tests** (`tests/unit/infrastructure/test_container.py`): mock config; `build_pipeline` returns correct types; provider switch changes adapter type.

**Completable in**: one session.

### [x] 4.2 — FastAPI application + routes

**Spec refs**: design §6 (API surface), REV-001 through REV-009, EXP-001 through EXP-008.
**Depends on**: 4.1.
**Deliverables**:

`backend/src/reconciliation/infrastructure/api/schemas.py`:
- `RunCreateResponse`: `run_id: str`, `status: str`.
- `RunStatusResponse`: `run_id: str`, `status: str`, `stage: str`, `page_count: int | None`.
- `ReconciliationRowDTO`: mirrors `ReconciliationRow` fields, all JSON-serializable (Decimal → str, date → ISO string).
- `EditRequest`: `field: str`, `new_value: str`.
- `ReassignRequest`: `new_registro: str`, `new_fecha: str`.
- `ExportRequest`: `fmt: Literal["xlsx","csv"] = "xlsx"`.

`backend/src/reconciliation/infrastructure/api/routes.py`:
- `POST /runs` (multipart `file: UploadFile`): saves PDF to temp location, `create_run`, runs `pipeline.run()` in background task. Returns `RunCreateResponse`.
- `GET /runs/{id}`: returns `RunStatusResponse`.
- `GET /runs/{id}/rows`: returns `list[ReconciliationRowDTO]`.
- `GET /runs/{id}/pages/{n}/thumb`: returns `FileResponse` for deskewed page PNG.
- `PATCH /runs/{id}/rows/{row_id}`: `EditRequest` → `review_service.apply_edit` → returns updated rows.
- `POST /runs/{id}/guias/{guia_id}/reassign`: `ReassignRequest` → `review_service.apply_reassignment` → returns updated rows.
- `POST /runs/{id}/export`: `ExportRequest` → `report_port.export` → `FileResponse`.

`backend/src/reconciliation/infrastructure/api/main.py`:
- `app = FastAPI(lifespan=lifespan)`. Lifespan: load `AppConfig`, call `build_pipeline`, store in `app.state`.
- CORS middleware for `localhost:5173` (Vite dev server).
- Include router.

**Tests** (`tests/integration/test_api.py`): `FastAPI TestClient`. Test all 7 endpoints: POST /runs (multipart), GET /runs/{id}, GET /runs/{id}/rows, PATCH edit, POST reassign, POST export. Use small fixture PDF + stub pipeline components.

**Completable in**: one session (may be a long session).

---

## Phase 5 — Frontend Features

> 5.1 → 5.2 → 5.3 → 5.4 → 5.5 loosely sequential (each component depends on prior). 5.1 and 5.2 can parallel.

### 5.1 — Design tokens + API client

**Spec refs**: design §5 (tokens), design §6 (API surface).
**Depends on**: Phase 0.3.
**Deliverables**:
- `frontend/src/design/tokens.css`: fill all variables: `--status-match: #22c55e`, `--status-mismatch: #ef4444`, `--status-flag: #f59e0b`, `--status-unclassified: #a3a3a3`, `--confidence-low: #f59e0b`, confidence scale, spacing (4/8/12/16/24/32/48), type scale, monospace font var.
- `frontend/src/api/types.ts`: TypeScript interfaces matching all 7 API responses: `RunCreateResponse`, `RunStatusResponse`, `ReconciliationRowDTO`, `EditRequest`, `ReassignRequest`, `ExportRequest`.
- `frontend/src/api/client.ts`: typed fetch functions for all 7 endpoints. Error handling: non-2xx → throw with response body.
- `frontend/src/composables/useReconciliationApi.ts`: TanStack Query composables: `useRunStatus(runId)`, `useReconciliationRows(runId)`, `useEditRow(runId)` mutation, `useReassignGuia(runId)` mutation, `useExport(runId)` mutation. Server state only; no UI state.

**Tests** (`frontend/src/api/__tests__/client.test.ts`): mock `fetch`; assert each function calls correct endpoint, deserializes response, throws on error.

**Completable in**: one session.

### 5.2 — Run upload + progress (features/run)

**Spec refs**: design §4 (pipeline sequence from UI perspective), design §5.
**Depends on**: 5.1, Phase 0.3.
**Deliverables**:
- `frontend/src/stores/run.ts`: `useRunStore()` with `uploadPdf(file: File)` action → calls `POST /runs` → stores `run_id`, `status`.
- `frontend/src/features/run/UploadPanel.vue`: file drag-drop or input, triggers `useRunStore().uploadPdf`. Shows upload state.
- `frontend/src/features/run/RunProgress.vue`: polls `useRunStatus(runId)` every 2s. Shows stage progress (`split → classify → deskew → extract → reconcile`). On `status=review` → `router.push(/runs/:id)`.

**Tests** (`frontend/src/features/run/__tests__/UploadPanel.test.ts`): mount component, simulate file drop, assert store action called. `RunProgress.test.ts`: mock useRunStatus, assert stage labels render, navigation called on `status=review`.

**Completable in**: one session.

### 5.3 — ReviewGrid + ReconciliationRow

**Spec refs**: REV-001, REV-002, REV-004, REV-005, REV-006, REV-007, design §5.
**Depends on**: 5.1.
**Deliverables**:
- `frontend/src/stores/reconciliation.ts`: `useReconciliationStore()`: `rows: ReconciliationRowDTO[]`, `dirtyEdits: Map<string, Partial<ReconciliationRowDTO>>`, `selectedRowId: string | null`, `filter: "ALL"|"MISMATCH"|"FLAGGED"`. Action: `setDirtyEdit(rowId, field, value)` — debounced PATCH via mutation.
- `frontend/src/features/review/ConfidenceBadge.vue`: props `confidence: float | null`, `threshold: float = 0.85`. Renders amber badge when below threshold. Icon + label (not color-only — a11y).
- `frontend/src/features/review/SourceThumb.vue`: props `runId: string`, `pageIdx: number`. Fetches `GET /runs/{id}/pages/{n}/thumb`, shows thumbnail.
- `frontend/src/features/review/ReconciliationRow.vue`: one row in the grid. Shows all 10 columns per spec EXP-002. Editable cells for `summed_qty` and `fecha`. Shows `ConfidenceBadge` per extracted value. `SourceThumb` on page index click. Status badge (MATCH green / MISMATCH red / UNCLASSIFIED amber / GUIA_MISSING / DECLARED_MISSING) — icon+label per a11y rule. On cell edit → `store.setDirtyEdit` → debounced PATCH.
- `frontend/src/features/review/ReviewGrid.vue`: virtualized (vue-virtual-scroller or CSS overflow) table grouped by `(registro, fecha)` collapsible sections. Renders `ReconciliationRow` per row. Filter bar (ALL / MISMATCH / FLAGGED). Uses `useReconciliationRows(runId)` for server state.

**Tests** (`frontend/src/features/review/__tests__/`):
- `ReviewGrid.test.ts`: mount with fixture rows, assert groups rendered, filter hides MATCH rows.
- `ReconciliationRow.test.ts`: assert 10 columns present, status badge variant, ConfidenceBadge shown for OCR values below threshold.
- `ConfidenceBadge.test.ts`: amber when below 0.85, normal otherwise.

**Completable in**: one session (long).

### 5.4 — GuiaReassignDialog + ExportButton

**Spec refs**: REV-003, REV-007, EXP-001 through EXP-008.
**Depends on**: 5.3.
**Deliverables**:
- `frontend/src/features/review/GuiaReassignDialog.vue`: modal dialog. Props: `guiaId: string`, `currentRegistro: string`, `currentFecha: string`. Inputs: new registro, new fecha. On submit → `useReassignGuia(runId)` mutation → invalidate rows query → both affected groups recompute and re-render. Motion: dialog open/close CSS transition. Records action in audit trail (backend handles).
- `frontend/src/features/review/ExportButton.vue`: button `POST /runs/{id}/export`. Accepts `fmt` prop (`xlsx`/`csv`). On success → triggers file download from response blob. Disabled while run status is not `review`.

**Tests** (`frontend/src/features/review/__tests__/`):
- `GuiaReassignDialog.test.ts`: submit calls mutation with correct payload, dialog closes on success.
- `ExportButton.test.ts`: click triggers mutation, download triggered.

**Completable in**: one session.

### 5.5 — Review page route wiring

**Spec refs**: REV-001 through REV-009, design §5.
**Depends on**: 5.2, 5.3, 5.4.
**Deliverables**:
- `frontend/src/app/router.ts`: `/ → UploadPanel + RunProgress`, `/runs/:id → ReviewPage`.
- `frontend/src/features/review/ReviewPage.vue` (new): composes `ReviewGrid` + `ExportButton` + `GuiaReassignDialog` (slot-driven). Shows page count from `RunStatusResponse`. On load: calls `useRunStatus`, `useReconciliationRows`.
- `frontend/src/app/App.vue`: top-level layout, `<RouterView>`, import `design/tokens.css`.
- `frontend/src/stores/run.ts` updated: on route load, if `run_id` in URL, restore run state from `GET /runs/{id}` (handles refresh).
- TanStack Query `QueryClientProvider` added in `main.ts`.

**Tests** (`frontend/src/features/review/__tests__/ReviewPage.test.ts`): mock TanStack Query, assert ReviewGrid and ExportButton rendered, page count shown.

**Completable in**: one session.

---

## Phase 6 — End-to-End Integration Tests

> Sequential, depends on all prior phases.

### 6.1 — Backend E2E: full pipeline happy path

**Spec refs**: INJ-S01 through INJ-S05, EXT-S01 through EXT-S12, REC-S01 through REC-S08, EXP-S01 through EXP-S06.
**Depends on**: Phase 4.2 complete.
**Deliverables** (`tests/integration/test_e2e.py`):
- **Fixture**: tiny 4-page PDF (created programmatically in test setup via PyMuPDF). Pages: 1 Protocolo declared page (digital text), 2 guía pages (scanned image with printed table), 1 Planilla Resumen (ignored).
- **Happy path test**: `POST /runs` with fixture PDF → poll until `status=review` → `GET /runs/{id}/rows` → assert rows non-empty, MATCH or MISMATCH present → `PATCH` edit one row → assert recomputed → `POST /runs/{id}/guias/{guia_id}/reassign` → assert both groups updated → `POST /runs/{id}/export?fmt=xlsx` → assert file downloaded, 10 columns.
- **Abort/resume test**: pipeline aborted after render stage; restart; assert cached renders reused (no re-render); pipeline completes.
- **Vision cap test**: set `max_vision_calls=1`, fixture with 2 guía pages; assert `VisionCapExceededError` raised at call 2; partial results preserved.

**Completable in**: one session.

### 6.2 — Backend E2E: error paths

**Spec refs**: INJ-S03, EXT-S04, EXT-S08, EXT-S09, EXT-S11, REC-S04, REC-S05.
**Depends on**: 6.1.
**Deliverables** (add to `tests/integration/test_e2e.py` or `test_e2e_errors.py`):
- Corrupt PDF → structured error, no output dir.
- Unclassified page → surfaces in rows with `kind=UNCLASSIFIED`.
- OCR confidence < 0.85 → row flagged `requires_review=True`.
- Vision null date → row flagged `requires_review=True`.
- DECLARED_MISSING: guía page with no declared counterpart.
- GUIA_MISSING: declared material with no guía.

**Completable in**: one session.

### 6.3 — Frontend Vitest smoke

**Spec refs**: REV-S01 through REV-S08b.
**Depends on**: Phase 5.5.
**Deliverables** (`frontend/src/features/review/__tests__/smoke.test.ts`):
- Mount `ReviewPage` with mocked TanStack Query (3 MISMATCH rows, 1 DECLARED_MISSING).
- Assert: 3 MISMATCH badges visible, 1 DECLARED_MISSING visible.
- Edit flow: simulate cell edit → mutation called → rows invalidated → new status shows.
- Reassign flow: open dialog → submit → both groups re-render.

**Completable in**: one session.

---

## Phase 7 — Config, Hardening, and Local-Run Polish

> Can start once Phase 4 + Phase 5 are functionally complete. Tasks are independent.

### 7.1 — config.yaml + .env.example

**Spec refs**: EXT-006, EXT-008, design §3.
**Depends on**: 2.1.
**Deliverables**:
- `backend/config.yaml` finalized with all fields (not just template from 2.1): all provider sub-configs, deskew, confidence, output_dir.
- `backend/.env.example`: `ANTHROPIC_API_KEY=`, `OPENAI_API_KEY=`, `VISION_PROVIDER=anthropic`, `MAX_VISION_CALLS=600`.
- `backend/src/reconciliation/application/config.py` updated to load `.env` via `python-dotenv` or pydantic-settings `env_file`.

**Completable in**: one session.

### 7.2 — Cost cap enforcement + audit logging

**Spec refs**: EXT-008, EXT-011, design §4.
**Depends on**: 2.3 (pipeline).
**Deliverables** (`backend/src/reconciliation/application/pipeline.py` updated):
- `vision_calls_budget` counter decremented per call; raises `VisionCapExceededError` (structured: `{calls_made, cap, pages_remaining}`) before submitting the (cap+1)-th call.
- Partial results (first N dates) preserved in sidecar.
- Audit record emitted: `{stage="vision", calls_made, cap_reached: bool}`.

**Tests** (`tests/unit/application/test_pipeline_cap.py`): assert error raised at exact cap+1; partial results accessible; audit record present.

**Completable in**: one session.

### 7.3 — Flagging surface completeness

**Spec refs**: REV-004, INJ-007 (orientation_fallback_failed), INJ-S04, INJ-S05, EXT-S08, EXT-S08b.
**Depends on**: 1.1 (models), 2.3 (pipeline), 4.2 (API).
**Deliverables**:
- `MaterialLine` + `PageClassification` + `ReconciliationRow` models audited for all required flag fields: `requires_review: bool = False`, `orientation_fallback_failed: bool = False`, `ocr_empty_after_deskew: bool = False`, `orientation_low_confidence: bool = False`.
- Pipeline sets each flag at the correct stage.
- `ReconciliationRowDTO` in API schemas exposes flags.
- `ReconciliationRow.vue` in frontend renders all 8 flag types per REV-004 (not color-only, icon+label).

**Tests**: unit test for each flag being set at the correct stage (pipeline unit tests).

**Completable in**: one session.

### 7.4 — Local dev run script

**Spec refs**: design migration/rollout (local-first).
**Depends on**: Phase 4.2, Phase 5.5.
**Deliverables**:
- `backend/Makefile` `run` target: `uvicorn reconciliation.infrastructure.api.main:app --reload --port 8000`.
- `frontend/package.json` `dev` script: `vite --port 5173`.
- `Makefile` at repo root: `dev` target runs both concurrently via `concurrently` or two terminal instructions in README.
- Smoke-test: `make dev` → backend answers `GET /` (health), frontend renders upload panel.

**Completable in**: one session.

---

## Task Dependency Summary

```
Phase 0 (scaffold) → Phase 1 (domain) → Phase 2 (application) → Phase 3 (adapters) → Phase 4 (infra) → Phase 5 (frontend) → Phase 6 (e2e) → Phase 7 (hardening)

Parallelism:
  Phase 0: 0.2 || 0.3 (backend deps vs frontend scaffold)
  Phase 1: 1.2 || 1.3 || 1.4 || 1.5 (domain components independent; 1.4 needs 1.3 for normalizer)
  Phase 2: 2.4 (ReviewService) || 2.3 (Pipeline) [both need 1.4 done; 2.4 does not wait for 2.3]
  Phase 3: 3.1 || 3.2 || 3.3 || 3.4 || 3.5 all parallel (all need only 1.5 ports)
           3.6 can start once 1.5 + 1.1 done (independent of 3.1–3.5)
  Phase 5: 5.1 || 5.2 can parallel; 5.3 → 5.4 → 5.5 sequential
  Phase 7: 7.1 || 7.2 || 7.3 || 7.4 all independent
```

---

## Review Workload Forecast

| Metric | Estimate |
|--------|----------|
| Estimated changed lines | ~3,800 (backend ~2,800: domain 400, application 600, adapters 700, infrastructure/API 700, tests 400; frontend ~1,000: stores/composables 200, components 500, tests 300) |
| 400-line budget risk | **High** (greenfield full-stack build, well above single-PR threshold) |
| Chained PRs recommended | **Yes** |
| Decision needed before apply | **Yes** |

**Recommended chain** (ask-on-risk delivery strategy applies — orchestrator must decide before launching apply):

| PR | Phases | Scope | Est. lines |
|----|--------|-------|------------|
| PR-1 | Phase 0 + Phase 1 | Scaffold + Domain core (pure, no framework) | ~600 |
| PR-2 | Phase 2 + Phase 3 partial (3.1, 3.4, 3.5) | Application layer + PDF adapter + text adapter + vision adapters | ~850 |
| PR-3 | Phase 3 partial (3.2, 3.3, 3.6) | Deskew + OCR + Excel report adapters | ~700 |
| PR-4 | Phase 4 | Infrastructure wiring + FastAPI | ~750 |
| PR-5 | Phase 5 | Frontend | ~1,000 |
| PR-6 | Phase 6 + Phase 7 | E2E tests + hardening | ~600 |

Each PR is independently reviewable and deployable. Domain core (PR-1) has zero adapter deps and is the safest merge-first candidate.
