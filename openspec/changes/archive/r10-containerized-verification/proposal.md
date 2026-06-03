# Proposal — r10-containerized-verification

**Change**: `r10-containerized-verification`
**Phase**: proposal (done) → spec / design (next, parallel)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-02
**Parent**: user strategy pivot (engram `architecture/cloud-vision-container-strategy` #2803); unblocks faithful verification of R8 (MATCH) + R9 (fecha-divergence, `architecture/reception-date-authority` #2709)

---

## 1. Intent

### Problem
Faithful verification of everything already implemented is **blocked by per-machine environment drift**, not by missing features. On this workstation the real-data e2e gate cannot run honestly:

- **PaddleOCR `predict()` is broken** — paddle 3.3.1 + paddleocr 3.6.0 on the CachyOS oneDNN/PIR CPU build raises `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support` (DECISIONS §rev-3 R6–R7). When it does grind, it pins **615% CPU for hours**, non-deterministically.
- **Swap pressure → SIGTERM** kills long runs before they complete.
- **Dependency installs vary per machine** — there is no frozen, reproducible environment, so "green" on one box means nothing on another (the suite once passed while the pipeline was broken — DECISIONS §audit).
- **Local `qwen3.5:9b` vision is slow and sequential** for the ~70 date reads the run needs.

Net effect: the OCR-validation gate — the whole point of the tool — cannot be exercised faithfully. The R8 MATCH work and the R9 handwritten-Protocolo-date divergence work are implemented but **unverified against real data** because no run finishes deterministically.

### Why now
R8 and R9 are the last functional gaps closed; the next required step is the **real-run gate**, and it is structurally impossible on the current host. Until the environment is reproducible, every verification result is suspect. The strategy is already user-decided (engram #2803) — this change is **scoping and packaging that decision**, not re-litigating it.

### Success looks like
- A **single `docker compose up`** runs the full backend test suite and a real-PDF pipeline run **deterministically**, identically on any machine, with no paddle, no GPU, and no per-host dependency drift.
- Guía quantities come from **SUNAT (deterministic)** with `ocr.enabled=false`; the ~70 date reads (35 guía + 35 Protocolo) run against **cloud vision** (`qwen3.5:397b-cloud`) via the existing provider-agnostic `VisionLLMPort` — a **config change, not an architecture change**.
- Cloud token consumption is **minimized and measurable**: each vision call sends only a tight region-of-interest crop (date stamp / Protocolo "Fecha:" field) at minimal resolution with tight `max_tokens`.
- The R8 #4252 MATCH case and the R9 fecha-divergence flag both reconcile in-container against real data.
- **Hexagonal / local-first invariants are intact**: the container is a deployment/packaging concern that never touches the domain core; cloud vision and SUNAT egress remain opt-in via config; the air-gap default stays documented.

---

## 2. Scope

### In scope
- **Backend Dockerfile** — multi-stage, `python:3.12-slim`, **uv** with a fully frozen lockfile (all deps pinned), non-root user, `.dockerignore`. **No paddle in the image** (OCR runs via the `ocr.enabled=false` flag, commit 1a7ef2b); **no GPU layers** (nvidia-container-toolkit is not installed).
- **`docker-compose.yml`** — `backend` + `frontend` services; the real input PDF mounted **read-only**; per-run output dir as a writable volume; vision pointed at the cloud endpoint via `base_url`; SUNAT opt-in (the only network-egress service).
- **Cloud vision config** — select the OpenAI-compatible provider with `model: qwen3.5:397b-cloud` and the Ollama cloud `base_url` (or host-Ollama proxy). Reuses the existing `VisionProviderConfig` / `VisionConfig` — no new adapter, no vendor binding.
- **ROI-crop consumption optimization** — extend the existing `StampCropConfig` approach: tune/confirm the guía date-stamp crop and the **Protocolo "Fecha:" crop** (`protocolo_crop`, currently a disabled zero-box default) so each cloud call sends only the precise region at minimal DPI, with tight `max_tokens` and `<think>`-stripping → minimal tokens per call. Wire measurement of per-call/aggregate token consumption.
- **SUNAT fetch optimization** — propose parallelization and/or run-dir caching for the ~35 sequential fetches (today: 30s timeout + backoff retry, observed **20–30 min** wall-clock on the host run) so the in-container faithful run completes in reasonable time. Framed as in-scope-to-consider; bounded by SUNAT rate-limiting.
- **Docs** — record cloud-vision + SUNAT egress as an explicit, opt-in deviation from the air-gap default (DECISIONS/HANDOFF), and the in-container run recipe.

### Out of scope (this change)
- **GPU passthrough** — nvidia-container-toolkit is not installed; cloud vision is precisely the reason no local GPU is needed.
- **Paddle in the container** — OCR stays off in-container; SUNAT supplies quantities. Fixing the paddle oneDNN/PIR build is a separate, orthogonal concern.
- **Kubernetes / K3s** — Compose is the correct tool for two services on one machine (infra-deploy decision tree).
- **Production deployment, CD, reverse proxy, TLS, monitoring, backups** — this is a **reproducible-verification** environment, not a hosted product. No Caddy, no Litestream, no CI deploy pipeline.
- **Domain-core changes** — grouping, matching, unit rules, and the OCR-validation gate are untouched. This change adds zero domain logic.
- **New vision providers or a new inference architecture** — cloud vision rides the existing `VisionLLMPort` + OpenAI-compatible adapter.

---

## 3. Approach (containerize the deployment edge; cloud vision + SUNAT as opt-in config)

The container is a **packaging / deployment adapter at the system edge** — it inverts nothing in the domain. The pipeline already depends only on ports; switching vision to cloud and OCR to the null path are **configuration selections** the existing seams support. No new architectural concept is introduced.

```
docker compose up
  ├── backend  (python:3.12-slim, uv frozen lockfile, non-root)
  │     ocr.enabled=false        → NullOcrExtractor (no paddle import, deterministic)
  │     vision.provider=openai   → VisionLLMPort → base_url = Ollama cloud
  │     │                            model qwen3.5:397b-cloud
  │     │                            ROI crop (stamp / Protocolo "Fecha:") → min DPI,
  │     │                            tight max_tokens, strip <think> → min tokens/call
  │     sunat.enabled=true       → SunatGreFetchPort (quantities; only network egress)
  │                                  + parallel/cached fetch (bottleneck mitigation)
  │     input PDF  → mounted READ-ONLY
  │     run output → writable volume (per-run isolation preserved)
  └── frontend (review grid)
```

| Concern | Mechanism | Existing seam reused |
|---------|-----------|----------------------|
| **Reproducibility** | `python:3.12-slim` + uv frozen lockfile, pinned base tag | infra-deploy multi-stage + pin rule |
| **No paddle / no GPU** | `ocr.enabled=false` → `NullOcrExtractor`; quantities from SUNAT | `OcrConfig` (1a7ef2b), `SunatConfig` |
| **Cloud vision** | `vision.provider=openai`, `base_url`=cloud, `model=qwen3.5:397b-cloud` | `VisionLLMPort` + `OpenAICompatibleVisionAdapter` (provider-agnostic) |
| **Token economy** | ROI crop at min DPI, tight `max_tokens`, `<think>` strip, consumption metering | `StampCropConfig` (guía stamp) + `protocolo_crop` (R9 Fecha) |
| **SUNAT throughput** | parallelize + cache the ~35 fetches within rate limits | `SunatConfig.cache`, fetch adapter |

### Key rationale
- **Compose, not K8s** (infra-deploy decision tree): two services, one machine, no autoscaling, no GitOps. K3s/K8s would be pure overhead.
- **Cloud vision is a config swap, not a redesign.** The port is already provider-agnostic (OpenAI-compatible `base_url`); pointing it at Ollama cloud changes one config block. The domain never learns a vendor exists — **no vendor binding** (CLAUDE.md provider-agnostic invariant).
- **Determinism comes from removing paddle and freezing deps**, not from new code. `ocr.enabled=false` injects `NullOcrExtractor` without importing paddle at all; SUNAT supplies the authoritative quantities deterministically. The uv frozen lockfile kills install drift.
- **ROI cropping is the cost-control lever.** The user holds the Ollama cloud plan specifically to **measure and optimize** consumption; sending tight crops instead of full pages is the difference between ~70 cheap calls and ~70 expensive ones. The R9 Protocolo crop is the missing piece (its config default is a disabled zero-box today).
- **Local-first stays the documented default.** Cloud vision and SUNAT egress are opt-in flags; the air-gapped path (local Ollama + working paddle/SUNAT-off) remains valid and documented. The container is a **deployment concern**, isolated from the domain core — reverting it changes no business logic.
- **SUNAT throughput is a verification-time constraint, not a correctness one.** Parallel/cached fetch is proposed to make the faithful run finish in minutes, bounded by SUNAT's rate-limiting and 30s/60s read behavior.

---

## 4. Risks & Mitigations

| Risk | Runtime trigger | What breaks if ignored | Mitigation |
|------|-----------------|------------------------|------------|
| **Cloud token cost / runaway consumption** | Full-page images or loose `max_tokens` sent to a 397B cloud model across ~70 calls | Unbounded bill; the cloud plan defeats its own purpose | ROI crop at min DPI + tight `max_tokens` + `<think>` strip; meter per-call and aggregate tokens; `max_vision_calls` cap already enforced; measure on a small page subset before the full run. |
| **Cloud-model vision accuracy on handwritten dates** | `qwen3.5:397b-cloud` reads a handwritten stamp / Protocolo "Fecha:" differently from the locally-validated `qwen3.5:9b` | Wrong reception date → false/missed R9 fecha-divergence flag | Reconciliation vs the trusted declared side is the gate (mismatches flag for human review, never auto-correct); validate the cloud read on the R7-proven pages (4,5,6,8,20,25,30) before trusting the full run; keep `protocolo_crop` tunable. |
| **Network dependency breaks the air-gap** | Container reaches Ollama cloud + SUNAT | Local-first invariant violated if treated as default | Cloud vision + SUNAT are opt-in flags, off by default; the air-gap path stays documented; this run is an explicit, recorded deviation (DECISIONS), not the product default. |
| **SUNAT rate-limit / sequential bottleneck** | ~35 fetches, 30s timeout + backoff, observed 20–30 min | Faithful in-container run is impractically slow or trips rate limits | Parallelize with a bounded concurrency window + run-dir cache (re-run reuses PDFs); back off on 429; keep fetch resilience (retry/backoff) intact. |
| **Cloud endpoint auth / availability** | Wrong `base_url`, missing/invalid cloud key, or cloud outage | Vision stage errors; run cannot read dates | api_key stays env-only (never serialized); validate connectivity at container start; degrade to a clear, surfaced error (not a silent empty read); host-Ollama proxy as fallback path. |
| **Dependency lock drift over time** | uv lockfile regenerated loosely, or base image floats | "Reproducible" image stops being reproducible | Pin the base tag (`python:3.12-slim`, never `latest`), commit the frozen uv lock, rebuild from lock only. |
| **`<think>` leakage / empty content from the cloud reasoning model** | 397B model emits `<think>` or empty content (R7 saw qwen empty-content) | Date parse failure → confidence 0.00 for affected guías | Reuse the R7 `<think>`-strip + empty-content fix; `temperature=0`; strict parse with a flagged-fallback, never a crash. |

---

## 5. Rollback / Abort plan
- **Additive and isolated.** This change adds a Dockerfile, a compose file, a cloud-vision config block, and ROI-crop tuning. It modifies **no domain code**. Deleting the container artifacts and config restores the prior local-run path exactly.
- **Config-reversible.** Setting `vision.provider` back to local Ollama, `ocr.enabled=true` (where paddle works), and `sunat.enabled=false` returns to the documented air-gap default with no code change.
- **Per-run isolation preserved.** The input PDF is mounted read-only; each run writes its own volume dir; aborting discards only that run's output.

---

## 6. Open questions (for spec/design)
- Exact cloud `base_url` shape — direct Ollama cloud endpoint vs. host-Ollama acting as a cloud proxy; auth header / key handling for the cloud plan via env-only secrets.
- `protocolo_crop` coordinates — the Protocolo "Fecha:" region must be tuned (its default is a disabled zero-box); which real pages calibrate it.
- Token-consumption metering surface — log per call, aggregate per run, or both; what target/ceiling (if any) gates the full run.
- SUNAT parallelization bound — safe concurrency level under SUNAT rate-limiting; whether caching alone is sufficient for a faithful single run.
- Whether the frontend service is needed for the verification run or backend-only suffices for the e2e gate (compose could scope frontend as optional).
- Image size / build-time budget for the frozen-deps backend image without paddle.
