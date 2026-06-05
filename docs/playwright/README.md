# docs/playwright — Playwright SA-5 runtime proofs

Local archive for **SA-5 visible-UX validation** artifacts (screenshots/snapshots)
captured when validating `frontend/src/**` changes against the running app, per
`CLAUDE.md` rule **SA-5** ("Visible-UX features require runtime validation before done").

## Why the images are NOT committed

Screenshots of the review UI contain **real QC data** — SUNAT GRE identifiers
(serie-número), Registro N°, RUCs, and material descriptions from the actual CTR
document. The repository `.gitignore` excludes `*.png` / `*.pdf` for exactly this
reason ("Sensitive: real QC data"). **Do not** add a tracking exception for these
images; keep the proofs **local-only** in this folder. This README is the only
tracked file here.

## Convention

- Save SA-5 proofs here as `pr<NN>-<feature>-<short-desc>.png` (e.g.
  `pr37-foundation-errored-guias-panel.png`).
- Point the Playwright MCP `browser_take_screenshot` output here instead of the
  project root, to keep the root clean.
- Record the validation **outcome** (what was checked, pass/fail, console errors)
  in the PR body and/or engram — that travels with the repo; the image is just
  the local visual backup.

## Proof log

| PR | Feature | Proof file (local) | Outcome |
|----|---------|--------------------|---------|
| #37 | guia-reprocess-staged-flow PR#1 foundation | `pr37-foundation-errored-guias-panel.png` | PASS — ErroredGuiasPanel renders 37 errored guías read-only, 0 console errors, table rows unchanged (run e6ea69df) |
