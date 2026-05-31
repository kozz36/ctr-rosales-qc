# HANDOFF — material-reconciliation (read this first on a new workstation)

> **Why this file exists:** the engram memory used during development is **local to the
> original machine** and does NOT travel with the repo. This document (plus the other
> files in `docs/`) is the versioned source of truth for continuing the work anywhere.

Last session: **2026-05-31**. Backend + frontend built; a design rev-2 delta is specced
and ready to implement. **Resume at the spec-delta slice (slice 0).**

---

## 1. What this project is

A local-first QC tool for a civil-engineering quality engineer. It ingests a 493-page
Autodesk Forma PDF export (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and
reconciles, per **Registro N° + fecha de recepción**, the **declared** materials (digital
text from the detail page Notes + Protocolo de Recepción) against the **summed** materials
from the scanned **guías de remisión**. It flags mismatches, lets the engineer reassign
misfiled guías, and exports the reconciled table to xlsx/csv.

Full domain context: `docs/DECISIONS.md`. Architecture: `docs/ARCHITECTURE.md`.

## 2. Current state (DAG)

```
proposal ✅  spec ✅(+delta pending)  design ✅ rev-2  tasks ✅(refresh pending)
apply ▶ 49/55 tasks — backend complete & e2e-validated, frontend complete
verify ⏳   judgment-day ⏳   archive ⏳
```

- **Backend**: 455 tests passing (domain + application + adapters + 9 real-PDF e2e).
  Runs: `cd backend && uvicorn reconciliation.infrastructure.api.main:app --reload`.
- **Frontend**: 85 vitest passing, 0 TS errors. `cd frontend && npm install && npm run dev`.
- **Heavy deps NOT installed** (lazy-loaded, optional extras): `paddleocr`, `anthropic`,
  `openai`, `pyzbar`, `zxing-cpp`. The suite is green without them.

## 3. RESUME HERE — exact next steps (in order)

1. **slice 0 — spec-delta.** Encode design rev-2 (sections A–F, see `docs/DECISIONS.md`
   §rev-2 and engram-mirror `design.md` A–F) into the openspec delta specs. Run sdd-spec
   in delta mode for the `material-reconciliation` change.
2. **Refresh tasks.md** to include the rev-2 work (it predates the delta).
3. **slice 1 — backend hotfix** (`docs/DECISIONS.md` §C-1/§C-2/§E + §QR):
   - Add `QrBarcodeExtractionAdapter` + `IdentityExtractionPort` (local QR → `guia_id = serie-numero`).
   - Multi-page guía **block grouping** (QR on first page propagates id to continuation pages).
   - `ReconciliationRow` exposes `contributing_guias`; reassign targets a real `guia_id`.
   - Replace the broken `summed_qty→field:'fecha'` edit with a guía-line `cantidad` edit.
   - `_derive_numero` returns `UNRESOLVED:<id>` (NEVER the Contents-ID) on parse failure.
   - **Fix test fixtures** in `test_reconciliation.py` / `test_models.py` that wrongly use
     `"4252"` (a section ID) as a registro numero — they would mask a regression.
4. **slice 2 — frontend hotfix**: guía drill-down in the grid, reassign by `guia_id`,
   line-cantidad edit, plus the smaller fixes: `:aria-rowcount` binding, thumbnail via API
   base (not `new Image()`), status column visible at 768px, neutralize the green badge on
   UNCLASSIFIED rows, localize "MISMATCH"→"Diferencia".
5. **Phase 6/7**: backend+frontend e2e happy/error paths, hardening, and implement
   `GET /runs/{run_id}/pages/{page}/thumbnail` (frontend already probes it and degrades).
6. **sdd-verify** (formal validation vs spec/design/tasks).
7. **judgment-day** (blind dual adversarial review + fix + re-judge) — MANDATORY gate.
8. **sdd-archive**.
9. **Deferred opt-in**: `SunatGreFetchAdapter` (Tier-4, off by default, breaks air-gap).

## 4. Hard-won lessons (do not relearn these)

- **Unit tests passed while the real pipeline was broken.** Always run a real-data e2e
  check (`backend/tests/integration/test_pipeline_e2e.py`) — it caught 5 blocking bugs.
- **Two identifiers exist**: Contents-ID `#4252` (section) ≠ Registro N° `232` (business
  key). Group by the Registro N°. The QR gives a third, deterministic id: `serie-numero`.
- **The grouping date is the HANDWRITTEN reception date** (vision-read), NOT the electronic
  GRE date. They can legitimately differ; a mismatch is the misfiled-guía case.
- **Units (KG/TN/RD/Rollo) are summed independently — never converted.**
- **Classify pages by TITLE, not supplier name** (Aceros Arequipa appears on non-guía sheets).

## 5. Engram → docs mapping (knowledge that was local-only, now versioned here)

| Engram topic | Versioned in |
|---|---|
| stack, domain-rules, llm-provider-abstraction | `docs/DECISIONS.md`, `docs/ARCHITECTURE.md` |
| design rev-2 (A–F), locked-defaults | `docs/DECISIONS.md`, `openspec/.../design.md` |
| e2e-integration-findings (5 bugs) | `docs/DECISIONS.md` §audit |
| frontend-review-findings (2 criticals) | `docs/DECISIONS.md` §frontend-review |
| qr-sunat-evaluation | `docs/DECISIONS.md` §QR |
| reception-date-authority | `docs/DECISIONS.md` §dates |
| delivery-roadmap | this file §3 |

If you re-enable engram on the new machine, re-import by reading these docs; nothing is lost.
