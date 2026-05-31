# Proposal — material-reconciliation

**Change**: `material-reconciliation`
**Phase**: proposal (done) → spec / design (next, parallel)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-05-31

---

## 1. Intent

### Problem
The QC engineer must verify that the materials physically received on site (printed on scanned **guías de remisión** from Corporación Aceros Arequipa) match what each reception record **declares** (digital text in the detail-page *Notes* and the *Protocolo de Recepción*). Today this is a manual cross-check across a **493-page Autodesk Forma PDF** (`CTR-PLC01-FR001 Recepción de Materiales en Obra`): 11 reception records (registros) each fanning out into multiple scanned, **90°-rotated** delivery notes. Manual reconciliation is slow, error-prone, and offers no audit trail. Misfiled guías (a delivery note that belongs to a different registro/fecha) are easy to miss.

### Why now
This is the first executable change for the greenfield tool. The stack and domain rules are decided (Hexagonal core, Python/FastAPI backend, Vue 3 frontend, dual OCR+vision extraction). The reconciliation engine is the product's reason to exist — everything else (UI, export) is scaffolding around it. We propose it now to lock the **pipeline contract** before spec and design fan out in parallel.

### Success looks like
- One command/upload turns the 493-page PDF into a **reconciliation table** ordered by `Registro N°` + `fecha de entrega`.
- Per `(registro, fecha, material_canonical, unidad)`: the **SUM of guía quantities** is shown next to the **declared** quantity, with a clear MATCH / MISMATCH flag.
- The engineer can **reassign a misfiled guía** (with its material) to the correct registro/fecha, and the table recomputes.
- The reconciled table **exports to xlsx/csv** with matches and relocated guías preserved.

---

## 2. Scope

### In scope
- PDF ingestion + page splitting/rendering (PyMuPDF) of the single 493-page export.
- **PageClassifier** — classify every page by **document TITLE** (not supplier name): `GUÍA DE REMISIÓN` (sums), `Protocolo de Recepción` / detail page (declared side), `Planilla Resumen` / `Listado de Barras` / photos / carátula / índice (ignored for summation).
- **Deskew** stage (PaddleOCR `DocImgOrientationClassification`) normalizing 0/90/180/270 rotation before OCR.
- **Dual extraction** behind a single `ExtractionPort`:
  - declared side from **digital text** (no OCR) — registro, fecha, material list with weights;
  - printed material+quantity tables from guías via **PaddleOCR**;
  - handwritten **fecha de recepción** on the stamp via a **provider-agnostic `VisionLLMPort`** — never bound to one SDK.
- **Provider-agnostic vision/LLM** (`VisionLLMPort`) selected by config (`provider: anthropic | openai | ollama`, with per-provider `model` / `base_url` / `api_key`):
  - `AnthropicVisionAdapter` (anthropic SDK, base64 image + Message Batches);
  - `OpenAICompatibleVisionAdapter` (openai SDK) — serves **both OpenAI cloud and local Ollama** by swapping `base_url` (`http://localhost:11434/v1`) + `model`; Ollama path keeps data 100% on-machine.
- **MaterialNormalizer** — canonicalizes the material **DESCRIPTION only**, never the unit.
- **ReconciliationService** (pure domain) — independent per-unit summation (KG/TN/RD/Rollo, no conversion), grouping key `(registro, fecha, material_canonical, unidad)`, MATCH/MISMATCH detection where reconciliation-vs-declared **is the validation gate** that surfaces OCR quantity errors.
- **Review** capability — Vue 3 editable grid to correct extracted values and **reassign guías** across registro/fecha.
- **Export** (`ReportPort`) to xlsx/csv.

### Out of scope (this change)
- Authentication, multi-user, RBAC (local-first, single engineer PC).
- Persistent database / historical storage across runs (in-memory + file artifacts for MVP).
- Multiple PDF templates or suppliers beyond the CTR-PLC01-FR001 / Aceros Arequipa layout.
- Unit conversion between KG/TN/RD/Rollo (explicitly forbidden by domain rule).
- Cloud deployment, CI/CD, containerization (deferred to infra phase).
- Automatic correction of mismatches — the tool **flags**; the engineer **decides**.

---

## 3. Approach (Hexagonal pipeline)

The domain core stays pure and framework-free. Extraction engines, the PDF reader, and the report writer are **adapters behind ports** (Dependency Inversion). The application layer orchestrates a deterministic single-pipeline flow — no agent/orchestration framework is warranted because the workflow is a fixed sequence.

```
split → classify → deskew → extract[OCR + vision] → normalize → reconcile → review → export
```

| Stage | Layer | Responsibility | Adapter / Port |
|-------|-------|----------------|----------------|
| **split** | adapter | Render 493 pages to images + pull digital text | `PdfStructureAdapter` (PyMuPDF) behind `DocumentSourcePort` |
| **classify** | domain + adapter | Tag each page by TITLE → guía / declared / ignored | `PageClassifier` (domain rule) |
| **deskew** | adapter | Normalize rotation 0/90/180/270 | `DeskewAdapter` (PaddleOCR orientation) |
| **extract** | adapter | Printed tables (OCR) + handwritten date (vision) + declared text | `PrintedTableAdapter` (OCR), `VisionLLMPort` (→ `AnthropicVisionAdapter` / `OpenAICompatibleVisionAdapter` for OpenAI+Ollama), `PdfStructureAdapter` — all behind `ExtractionPort` |
| **normalize** | domain | Canonicalize material description (unit untouched) | `MaterialNormalizer` |
| **reconcile** | domain | Per-unit independent SUM vs declared; MATCH/MISMATCH | `ReconciliationService` |
| **review** | application + UI | Edit values, reassign guías, recompute | FastAPI command + Vue grid |
| **export** | adapter | xlsx/csv of reconciled table | `ExcelReportAdapter` behind `ReportPort` |

### Key rationale
- **Declared side needs no OCR.** The *Protocolo de Recepción* is digital text duplicating the *Notes* list — it is the trusted reference. This makes reconciliation an asymmetric check: trusted declared vs noisy extracted, which is exactly why **reconciliation doubles as the OCR validation gate**.
- **DUAL extraction, not one model.** Printed tables are PaddleOCR's strength (cheap, local, deterministic-ish). Handwritten dates on a stamp need multimodal reasoning → a vision LLM, batched to control cost. Splitting the two keeps the expensive path scoped to the *only* field that needs it.
- **Provider-agnostic vision (Strategy + Dependency Inversion).** The vision adapter sits behind `VisionLLMPort`; the domain never imports a vendor SDK. Switching Anthropic ↔ OpenAI ↔ Ollama is a config change, not a code change. Ollama (local) makes the tool fully air-gapped at the cost of weaker handwriting accuracy — acceptable because the reconciliation gate + human review, not the model, are the accuracy guarantee.
- **Per-unit independent summation** is a domain invariant, not an implementation detail — the grouping key carries `unidad` and the normalizer is forbidden from touching it.
- **Classify by title** because Aceros Arequipa branding appears on non-guía sheets (Planilla Resumen, Listado de Barras) — supplier-name classification would produce false-positive sums.

---

## 4. Risks & Mitigations

> **Assumption (per config rule):** OCR and vision are imperfect. The design treats every extracted quantity and date as *unverified until reconciled or human-confirmed*. We do not assume any target accuracy number for MVP; the reconciliation gate + human review are the accuracy guarantee, not the models.

| Risk | Runtime trigger | What breaks if ignored | Mitigation |
|------|-----------------|------------------------|------------|
| **OCR quantity accuracy** | PaddleOCR misreads a digit (e.g. 1.250 → 1.260) on a low-quality scan | Wrong SUM → false MISMATCH or, worse, false MATCH | Reconciliation-vs-declared is the gate; flagged rows are human-reviewable in the grid; show raw OCR confidence + source page thumbnail next to each value. |
| **Handwritten date accuracy** | Claude misreads the stamped reception date | Guía grouped under wrong fecha → spurious mismatch | Date is editable in review; cross-check against the digital Protocolo fecha when present; allow the engineer to override and reassign. |
| **Vision cost over hundreds of pages** | Naively sending all 469 scanned pages to vision | Cost blowout, slow runs | Vision is scoped to the **handwritten date field on guía pages only** (a small subset, after classification); never send non-guía pages. Batching is **provider-specific** (Anthropic Message Batches / OpenAI Batch API / Ollama sequential local) — exposed as a `VisionLLMPort` capability, with sequential fallback when a provider has no batch API. |
| **Vision provider quality variance** | Local Ollama model misreads a handwritten date that Claude/GPT would get | Wrong fecha grouping on the cheap/private path | Provider is configurable with a sane cloud default; Ollama is the opt-in private fallback; the reconciliation gate + editable review absorb provider weakness; surface per-value confidence regardless of provider. |
| **Page misclassification** | A page title is OCR-garbled or a layout variant appears | A guía is dropped (under-sum) or a Planilla is summed (over-sum) | Classify by title with explicit allow/deny title rules; surface an "unclassified / low-confidence" bucket in review so nothing is silently dropped. |
| **90° rotation not corrected** | Deskew misjudges orientation | Table OCR fails entirely on that page | Run orientation classification before OCR; flag pages where post-deskew OCR yields empty tables for manual rotation. |
| **Misfiled guías** | A delivery note physically belongs to another registro | Both registros mismatch | First-class **reassign** action in the domain + UI; reassignment recomputes both affected groups. |

---

## 5. Rollback / Abort plan

The change is **greenfield and local-first** — there is no production system to corrupt, which bounds blast radius to the engineer's machine and the generated artifacts.

- **Per-run isolation**: each ingestion run writes to its own output directory (input PDF untouched, read-only). Aborting a run discards only that directory.
- **No destructive state**: MVP holds reconciliation state in-memory + per-run files; there is no shared DB migration to reverse.
- **Stage-level abort**: the pipeline is sequential and idempotent per stage. If extraction or vision fails (API outage, cost cap), the run aborts cleanly after classify/deskew with cached page renders preserved, so a re-run does not re-split.
- **Vision cost cap**: a hard cap on batched vision calls aborts the run before overspend; the engineer can fall back to manual date entry in review.
- **Code rollback**: standard VCS revert of the change branch; no data migration, no external service to decommission.

---

## 6. Open questions (for spec/design)
- Exact xlsx layout and column set for the export (declared, summed, delta, flag, source pages).
- Confidence-threshold policy: at what OCR/vision confidence does a value auto-flag for review vs pass silently.
- Whether deskew runs on all scanned pages or only post-classification guía pages (cost vs completeness).
- Persistence boundary for review edits (in-memory only vs per-run sidecar file) — affects resumability.
- Default vision provider for MVP (cloud Anthropic/OpenAI for accuracy vs Ollama for zero-cost privacy) and the exact `VisionLLMPort` contract (batch capability flag, structured `{date, confidence}` return).
