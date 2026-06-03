# Material matching — declared (Forma) ↔ guía (SUNAT) canonical key

> Durable mirror of the local skill `.claude/skills/material-canonical-matching` (skills dir is
> gitignored, so this doc carries the domain knowledge with the repo). Drives the rev-3 **R8**
> MATCH-resolution work. See `docs/DECISIONS.md` §known-open and engram `match-resolution-gap`.

## Problem
Declared (Autodesk Forma text) and guía (SUNAT GRE PDF / OCR) name the SAME physical rebar with
DIFFERENT text → exact-string grouping = zero MATCH. Match on a **canonical key**, not raw text.

Real proof (section #4252): declared `BARRA AG615/A706 G60 1/2" x 9M = 4.124 TN` = guías on
pages 5+6+8 summing 4.124 TN → same item, different descriptions.

## Canonical key
`(familia, grado, diámetro, presentación, unidad)` — e.g. `BARRA · A615 G60 · 1/2" · 9M · TN`.

- **familia**: `BARRA` (acero corrugado). `acero dimensionado` = BARRA cut/bent.
- **grado**: collapse `a615`, `a615/a706 g60`, `ag615/a706 g60`, `a a615-g60` → **`A615 G60`** (Aceros Arequipa rebar is dual-grade A615/A706 G60).
- **diámetro**: one of `8mm, 3/8", 1/2", 5/8", 3/4", 1", 1 3/8"`. Regex: `(\d+\s+\d+/\d+|\d+/\d+|\d+\s?mm|\d+)\s*("|pulg|mm)?`.
- **presentación** (SIGNIFICANT — never merge across): `9M` (straight 9 m bar, from `x 9m`) vs `DOB` (cut/bent, from `dob`/`dimensionado`/`apl`).
- **unidad**: KG/TN/RD/Rollo — **never converted**; part of the key.

Real declared↔SUNAT pairs:
| Declared | Guía (SUNAT) | Key |
|---|---|---|
| `barra ag615/a706 g60 1/2" x 9m` | `barra a a615-g60 1/2" x 9m` | `BARRA A615 G60 1/2" 9M` |
| `barra a615/a706 g60 1" (dob)` | `acero dimensionado - barra a615 g60 1" dob apl` | `BARRA A615 G60 1" DOB` |

## Matching strategy (R8)
1. Normalize both sides to the canonical key (deterministic regex first).
2. **Ambiguous descriptions** → local-LLM inference (Ollama `qwen3.5:9b`, temp 0, strip `<think>`, return JSON tuple) → flag `requires_review` + `match_method=llm_inferred`.
3. **SUNAT `código producto`** (e.g. 407797) is an authoritative join key if a declared↔código map is ever supplied (Forma side currently has no código).
4. Group declared + guía contributions by key + unidad; sum guía `cantidad`; compare to declared **EXACT (0)** → MATCH / MISMATCH; unmatched → DECLARED_MISSING / GUIA_MISSING.
5. `fecha` is NOT in the material key — group by (registro, key); a fecha divergence is the misfiled-guía signal, handled separately.

## Open design decision (user)
Whether to do this as deterministic-only, deterministic+LLM-fallback, or a small dedicated mini-SDD
(its own change). Recommended: deterministic tuple parse as primary (fast, auditable) + LLM fallback
for the long tail, behind the OCR-validation-gate (LLM-inferred rows always flagged for human review).
