# Archive Report — Close-Out Rev3b (base + R8 + R9 + R10)

**Date**: 2026-06-03
**Judgment-Day Gates**: APPROVED — (a) R8+R9+r10 core (3 rounds) + (b) rev-2 base areas (2 rounds), all blind dual judges
**Artifact Store**: hybrid (openspec + engram)
**Status**: FOUR CHANGES ARCHIVED — 8 capability specs promoted 1:1 to the spec store

---

## Summary

Archiving four interdependent SDD changes that complete the reconciliation product:

1. **material-reconciliation** (base rev-2/rev-3 core) — state.yaml = done
2. **r8-material-matching** — canonical material key (MAT-001..MAT-013)
3. **r9-fecha-divergence-review** — handwritten Protocolo date authority (FDR-001..FDR-011)
4. **r10-containerized-verification** — paddle-free cloud-vision env (CONT-001..CONT-008)

---

## Promotion Strategy — 1:1 per-capability (NOT folded)

Each change folder's delta spec is a self-contained full spec per capability/domain (no
`## ADDED/MODIFIED` markers). Promotion is therefore a faithful 1:1 copy of each capability's
`spec.md` into `openspec/specs/<capability>/spec.md`. The cross-cutting concern — `fecha`
removed from the reconciliation grouping key (rev-3 R8/MAT-001) — is captured as a dated
SUPERSEDED delta note inside the promoted `reconciliation/spec.md` (source commit `fe5fdf5`).

### Capability specs promoted to `openspec/specs/`

| Capability | Source change | Lines | Requirements |
|---|---|---|---|
| `reconciliation` | material-reconciliation | 433 | REC-001..REC-010, REC-C01..C09, REC-S01..S08 (+ fecha-removal delta note) |
| `extraction` | material-reconciliation | 847 | EXT-* |
| `ingestion` | material-reconciliation | 134 | ING-* (incl. rev-2 QR identity tier) |
| `review` | material-reconciliation | 365 | REV-* |
| `export` | material-reconciliation | 154 | EXP-* |
| `material-matching` | r8-material-matching | 409 | MAT-001..MAT-013, MAT-S01..S12 |
| `fecha-divergence` | r9-fecha-divergence-review | 428 | FDR-001..FDR-011, FDR-S01..S19 |
| `containerized-verification` | r10-containerized-verification | 233 | CONT-001..CONT-008, CONT-S01..S15 |

Total: 3003 lines across 8 capability specs. Each is a verbatim promotion of its source
(reconciliation = source 422 + 11-line fecha-removal note). The original delta specs remain
inside the archived change folders for full traceability.

---

## Requirement Traceability

### material-reconciliation (base, rev-2/rev-3)
- `reconciliation` (REC-*): grouping, summed quantities per unit (never converted), MATCH/MISMATCH, confidence flagging, multi-page guía blocks, reassignment.
- `extraction` (EXT-*): digital-text + OCR extraction, classifier (by TITLE / QR, section-ID guard).
- `ingestion` (ING-*): PDF read-only ingestion, page render, rev-2 QR identity tier (GuiaIdentity, serie-numero).
- `review` (REV-*): ReviewService, reassign misfiled guías, thumbnail API.
- `export` (EXP-*): xlsx/csv export, Método + Revisión columns.
- **Key decision**: `fecha` removed from the grouping key (R8/MAT-001); fecha divergence is a post-grouping side-channel (FDR-011).

### R8 — Material Canonical Matching (`material-matching`, MAT-001..MAT-013)
- Grouping key = `(registro, material_canonical, unidad)` — `fecha` NOT included (MAT-001).
- Canonical key value object (MAT-002); grade collapse (MAT-003); diameter canonical set (MAT-004); presentación "9M" vs "DOB" never merged (MAT-005); LLM fallback via `MaterialInferencePort` (MAT-006/007); worst-wins `match_method` + `requires_review` (MAT-008); real-data acceptance #4252 → MATCH 4.124 TN deterministic (MAT-013).

### R9 — Fecha Divergence Review (`fecha-divergence`, FDR-001..FDR-011)
- Declared reception date authority = handwritten Protocolo date, vision-read (FDR-001).
- Divergence predicate day-month primary; year-only = no WARNING (FDR-003).
- Non-blocking; material MATCH unaffected (FDR-004, FDR-011).
- Low-confidence (<0.85)/null date → registro flag, no guía warnings (FDR-005/007).
- API + frontend red highlight per guía and per registro (FDR-008/009); pure domain check (FDR-010).

### R10 — Containerized Verification (`containerized-verification`, CONT-001..CONT-008)
- Reproducible paddle-free image (CONT-001); in-container tests (CONT-002); cloud vision config-only (CONT-003); `ocr.enabled=false` → no paddle (CONT-004); vision ROI crops + token logging (CONT-005); bounded-concurrency SUNAT + cache (CONT-006); R8+R9 gates in-container (CONT-007); local-first air-gap preserved (CONT-008).

---

## Engram Topic Keys

| Change | Proposal | Spec | Design | Tasks | Verify |
|---|---|---|---|---|---|
| material-reconciliation | #2688 | #2691 | #2690 | #2693 | base verify-report `sdd/material-reconciliation/verify-report-base` (#2823) |
| r8-material-matching | #2772 | #2773 | #2774 | #2775 | #2787 |
| r9-fecha-divergence-review | #2790 | #2791 | #2792 | #2793 | — |
| r10-containerized-verification | #2804 | #2805 | #2806 | #2807 | — |

---

## Judgment-Day Verdicts (both APPROVED)

### (a) R8 + R9 + r10 core — APPROVED, 3 rounds (engram `sdd/close-out-rev3b/judgment-day` #2821)
- R1: 3 CRITICAL (C1 stale gate test, C2-A cross-registro pollution, C2-B declared-date JSON corruption) + 3 WARNING + KI-1.
- R2: confirmed C2-B was asymmetric (guía side still buggy) → fixed W-1/W-2 + untracked a stray junk file.
- R3: APPROVED, zero regressions. Fix commits `7e5f897..ba3b0c5`, `596704f..182d72a`, `a3069ad`.

### (b) rev-2 base areas — APPROVED, 2 rounds (engram `sdd/close-out-rev3b/jd-base-areas` #2825)
Targeted at the areas the core JD under-focused (ReviewService/reassign, QR identity tier, export/thumbnail).
- R1: REJECTED — B1 (guía line-edit always HTTP 422, dead feature), B2 (guia_line_edit not replayed on restart → data loss), B3 (vision_audit destroyed on first review mutation), B4 (section-ID accepted as Registro N°), B5/B6 warnings. QR identity tier + export confirmed CLEAN.
- R2: APPROVED, zero regressions. Fix commits `010036c`, `ca65b0b`, `a0aeb99`, `fe5fdf5`, `5d2280c`.

Also fixed this close-out: the stale `test_unclassified_pages_in_classifications` (commit `0502533`) — scanned guía pages now correctly classify as GUIA via local QR (dddd458), so the real-PDF integration suite is green.

---

## Recorded KNOWN-OPEN Items (non-blocking, deferred)

- **KI-4 — Faithful cloud-vision/SUNAT e2e gate (DEFERRED).** CONT-007 acceptance (R8 MATCH #4252=4.124 TN + R9 divergence Registro 232) NOT yet captured on real data with cloud vision + SUNAT in a quiet window. User decision: run BEFORE the final push. Logic verified via unit tests + gates; only the live e2e signal is pending.
- **W2-B — SUNAT fetch pacing (theoretical).** `fetch_many` dispatch gate + per-thread lock may over-serialize the network phase (~1 req/0.5s). Follow-up optimization only.
- **SUGGESTION — `_parse_day_month` docstring stale** (`pipeline.py:~1665`); code correct.
- **INFO (base JD)** — redundant in-loop `Decimal` import in `review_service.py` replay (has `# noqa`); `GuiaDrillDown.test.ts` render mounts omit the now-required `materialCanonical` prop (non-fatal).

---

## Archive Folder Structure

```
openspec/changes/archive/
├── close-out-rev3b-archive-report.md   (this file)
├── material-reconciliation/   (proposal, design, tasks, state.yaml, specs/{extraction,ingestion,reconciliation,review,export})
├── r8-material-matching/      (proposal, design, tasks, specs/material-matching)
├── r9-fecha-divergence-review/(proposal, design, tasks, verify-report, specs/fecha-divergence)
└── r10-containerized-verification/ (proposal, design, tasks, specs/containerized-verification)
```

---

## Next Steps

1. ✅ 8 capability specs promoted 1:1 to `openspec/specs/`.
2. ✅ Four change folders moved to `openspec/changes/archive/` (staged via `git mv`).
3. ✅ Archive report written (this file) + persisted to engram (`sdd/close-out-rev3b/archive-report`).
4. → User reviews the staged diff, then commits the archive.
5. → Quiet-window faithful e2e run (KI-4) — before push.
6. → Visual validation (Playwright + vision) — LAST, per HANDOFF §3 REVISED.
7. → Push `feat/rev2-identity-domain` (user-gated).

---

**Archive complete** (pending the user's commit of the staged moves + spec promotion).
Branch: `feat/rev2-identity-domain` · Judgment-Day: APPROVED (core + base) · KI-4 e2e + visual deferred to pre-push.
