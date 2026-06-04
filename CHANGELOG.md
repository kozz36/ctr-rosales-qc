# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-06-04

First stable release. The tool is fully operational for its core use case: reconciling
declared construction materials (Autodesk Forma digital text) against scanned SUNAT GRE
guías de remisión, grouped by Registro N°, with human-review flagging and XLSX/CSV export.

### Notes

**Recommended launch mode for v1.0.0**: deterministic vision-off + SUNAT-authoritative date
mode (`vision.enabled=false`, `sunat.enabled=true`). In this mode there are zero LLM calls —
guía reception dates resolve directly to the SUNAT `fecha_entrega` delivery date via the R9b
Rule-2 floor, and material quantities come from SUNAT GRE data. The pipeline is fully
deterministic and air-gap-friendly once the SUNAT cache is warm. Dates are still compared
against the digital Protocolo `Fecha:` authority and divergences are flagged for human review.
See `docs/USAGE.md` for operating modes and `install.sh` / `make app-up` for the one-command
Docker launch.

### Added

- **Page-sheet viewer** (`PageSheetViewer.vue`): full-resolution (200 DPI) lightbox triggered
  by clicking a source-page chip in the review table. Ships with zoom (50% steps, 100–400%),
  rotate (90° steps), reset, and a hand/pan tool to drag the zoomed image at client-side CSS
  transform speed — zero extra network calls after initial load. Backend: `GET
  /runs/{run_id}/pages/{page}/image` sibling endpoint at 200 DPI (thumbnail stays at 120 DPI).
  Persistent page-number overlay badge on every source-page chip (#27, PR #30).
- **a11y — viewer focus management** (WCAG 2.4.3): `PageSheetViewer.vue` captures
  `document.activeElement` on open and restores focus to the chip trigger on close; a
  focus-trap (`onTab`) keeps Tab/Shift+Tab cycling within the dialog without escaping to
  background content; zoom `+`/`-` key bindings unified to a single `@keydown="onKeydown"`
  handler comparing `event.key` directly (`+`/`=` zoom in, `-`/`_` zoom out), removing the
  layout-dependent Shift modifier (#31, PR #32).
- **SUNAT-authoritative date mode** (`vision.enabled=false`): `NullVisionAdapter` wires
  zero LLM calls; guía dates resolve to SUNAT `fecha_entrega` via R9b Rule-2 floor; fully
  deterministic, air-gap-friendly once cache is warm. `AppConfig` enforces fail-fast when
  both vision and SUNAT are disabled (no date source). Symmetric to the existing
  `ocr.enabled=false` escape hatch (#11).
- **Canonical material key — R8** (`MaterialKeyNormalizer`): deterministic-primary normalizer
  resolves declared and guía material descriptions to a shared canonical key (grade, diameter,
  length, unit) so declared↔guía contributions group correctly. LLM-fallback path behind
  `MaterialInferencePort` for the ambiguous tail; LLM-inferred rows always flagged
  `requires_review`. Eliminates the zero-MATCH gap on real data (#4252 BARRA A615 G60 1/2" ×
  9M = 4.124 TN MATCH confirmed on real PDF).
- **Reception-date authority — R9**: digital Protocolo `Fecha:` (deterministically parsed from
  the PDF text layer, no vision) is the authoritative declared date per Registro N°. Per-guía
  handwritten date (vision-read) compared day-month against the Protocolo; divergence →
  non-blocking `requires_review` WARNING with page number and red highlight. Bounded year
  inference applied to the guía side (vision-year is unreliable).
- **Reception-date floor and ceiling — R9b/R9c**: SUNAT `fecha_entrega` is the physical lower
  bound (goods cannot be received before delivery); Protocolo date is the upper bound. Dates
  outside the `[floor, ceiling]` bracket are clamped with a `requires_review` WARNING. Crossed
  bounds (delivery after Protocolo — physically impossible) are surfaced as a distinct
  `delivery_after_protocolo` WARNING and never clamped.
- **Containerized verification — r10**: paddle-free Docker environment (`ocr.enabled=false`,
  `Dockerfile` + `docker-compose.yml`); bounded-concurrency SUNAT fetch; provider-agnostic
  cloud vision (Ollama `qwen3.5:397b-cloud` via `base_url`, configurable via env vars).
  `make` targets for build, up, gate, and down.
- **XLSX / CSV export** (13 columns): reconciliation table exportable from the review UI;
  includes Registro N°, material canonical, unit, declared qty, guía qty, delta, status,
  confidence, page refs, and divergence flags.
- **Guía drill-down + reassign** in the review table: full-row click expands guía-level detail;
  reassign and line-edit actions moved into the drill-down.
- **Full-row click** toggles guía drill-down for discoverability (aria-expanded exposed).
- **Determinate progress bar** (`RunProgress.vue`): stage label, per-item count, elapsed time,
  and ETA during pipeline runs. Strictly observational — byte-identical output when
  `progress_cb=None`.
- **Real-PDF gate** (25-page subset, cloud vision): `TestR9RealPDFGate` 5/5 passing; #4252
  1/2"×9M = 4.124 TN MATCH deterministic + R9 divergence confirmed on pages 1–25.
- **Operator usage guide** (`docs/USAGE.md`): run commands, operating modes, the upload →
  review → reassign → export flow, and how to read the review table.

### Changed

- **Reconciliation grouping key** now `(Registro N°, material_canonical, unidad)` — `fecha`
  removed as a grouping axis (R8 domain rule MAT-001). Including `fecha` previously split
  declared↔guía groups whenever the vision-read date differed (year unreliable), producing
  zero MATCH. This is a breaking change to the grouping semantics (no data migration needed —
  outputs are derived).
- **Vision provider-agnostic**: vision calls go through `VisionLLMPort`; provider selected by
  config (`provider: anthropic | openai | ollama`). Anthropic SDK, OpenAI SDK, and Ollama via
  `base_url` are all supported with no domain binding.
- **`VisionConfig.disable_thinking` default changed `False → True`**: eliminates ~12 s/call
  `<think>` overhead on `qwen3.5` without measurable accuracy loss for structured
  OCR/date-extraction tasks.
- **Protocolo `Fecha:` parsed from digital text layer**, not vision. Prior implementation
  erroneously assumed the Protocolo date was handwritten (vision-read); corrected in R9 —
  the printed `Fecha:` field is deterministic text, no model call.
- **Thumbnail falls back to source PDF** when deskewed PNG is absent (fixes 404 on
  OCR-off / vision-off runs).
- **SUNAT progress reported per-wave during fetch** (not after the full batch), preventing the
  progress bar from freezing at 50% for the duration of the SUNAT network call.

### Fixed

- **Review table width** (#23): removed dead `Acciones` column (reassign moved to drill-down
  long ago, leaving an empty column header), corrected group-row colspan from 13 to 11, and
  made `Material` the sole `width: auto` column so the table fills 100% width with no empty
  right band.
- **Canonical diameter `1.3/8` dot-separator** (#28): SUNAT GRE (Corporación Aceros Arequipa)
  writes the whole/fraction separator as a dot (`1.3/8"` instead of `1 3/8"`). The normalizer
  now accepts `\b1\s*[.\-]?\s*3/8` and canonicalizes to `1 3/8"`. Confirmed e2e on real data:
  Registro 232 `1 3/8" DOB` MATCH 0.628, `3/8" DOB` correctly de-contaminated.
- **Red chip glow for `fecha_divergence` pages** in the `SourcePages` component (R9 visual
  indicator for guías that require review).
- **Confidence badge overflow** into PÁGINAS ORIGEN cell prevented (`flex-wrap` on confidence
  cell).
- **Group-row collapse** replaced `v-show` with `v-if` so collapsed rows are removed from the
  DOM (not just hidden).
- **`guia_id` reassign idempotency** and section-ID-as-Registro guard.
- **Guía line-edit HTTP 422** recovered (was silently broken behind a green test suite — root
  cause: field name mismatch in the request body schema).

---

[1.0.0]: https://github.com/kozz36/ctr-rosales-qc/releases/tag/v1.0.0
