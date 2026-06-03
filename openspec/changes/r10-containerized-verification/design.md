# Technical Design — r10-containerized-verification

**Change**: `r10-containerized-verification`
**Phase**: design (this doc) — architecture-level HOW, no code
**Artifact store**: hybrid (engram `sdd/r10-containerized-verification/design` + this file)
**Reads**: proposal (engram #2804 / `proposal.md`), strategy decision (engram #2803)
**Date**: 2026-06-02

> Design only. This is the architectural HOW; concrete task steps belong to `sdd-tasks`.
> Domain core stays PURE — every change here lives at the system edge (Dockerfile, compose,
> config selection, one adapter-internal concurrency change). Pattern vocabulary used:
> **Deployment Adapter at the system edge**, **Dependency-Inversion via config (Strategy
> factory)**, **Region-of-Interest (ROI) extraction**, **Bounded-Concurrency (semaphore)**,
> **Cache-aside**.

---

## 0. Grounded code facts (verified, not assumed)

| Fact | Evidence |
|------|----------|
| Deps split into extras: `ml` (paddle), `llm` (anthropic+openai), `identity` (pyzbar+zxing+Pillow+numpy), `dev`. Core has fastapi/uvicorn/pydantic/pymupdf/openpyxl/polars/pyyaml/python-multipart. | `backend/pyproject.toml:18-45` |
| **Paddle is pulled ONLY by the `ml` extra** (`paddlepaddle`, `paddleocr`). Excluding `ml` excludes paddle/oneDNN/PIR entirely. | `pyproject.toml:19-23` |
| `uv.lock` **already exists** (untracked at repo root). Reproducible pinning is available now; it must be committed. | `Glob uv.lock` → present |
| Vision is provider-agnostic behind a factory. `provider=openai` → `OpenAICompatibleVisionAdapter(supports_batch=True)`. `provider=ollama` → same adapter, `base_url` swap, `supports_batch=False`. | `adapters/vision/factory.py:59-85` |
| The OpenAI-compatible adapter already swaps `base_url`, takes `api_key`, has `max_tokens` (default 4096), and **already strips `<think>` blocks** + defensive JSON parse (R7 fix). | `adapters/vision/openai_compatible.py:46-85,136-185` |
| `read_handwritten_date_batch` with `supports_batch=False` runs **sequential** `read_handwritten_date` calls. The OpenAI `supports_batch=True` path uses the cloud **Batch API** (24h window, polls 10 min). | `openai_compatible.py:187-216,238-290` |
| Vision stage calls one read **per block** on the first-page image, after applying `stamp_crop` (`_prepare_vision_image`) or `protocolo_crop` (`_prepare_protocolo_vision_image`). | `pipeline.py:1008-1067,1483-1494` |
| `StampCropConfig` is fractional `(x0,y0,x1,y1)` ∈ [0,1] with `.enabled` = non-degenerate box. `stamp_crop` default = upper-right `(0.55,0.05,1.0,0.45)` (R7-proven). `protocolo_crop` default = **disabled zero-box** `(0,0,0,0)` → falls back to ≥`fallback_dpi`(300) full page. | `config.py:42-106` |
| SUNAT adapter: lazy httpx, **graceful None** (never raises into pipeline), **cache-aside** to `cache_dir/{key}.pdf`, retry+backoff, structured `httpx.Timeout(connect≤10s, read=60s)`, and a `_FETCH_PACING_S=0.5` inter-request sleep. | `adapters/sunat/descargaqr.py:159-374` |
| SUNAT fetch loop is **strictly sequential** (`for block in blocks: self._sunat.fetch(...)`), synchronous, inside the pipeline. | `pipeline.py:909-976` |
| `build_pipeline` wires cache_dir = `ctx.run_dir/"sunat"` when `sunat.cache`. Cache lives **inside the per-run dir** → not reused across runs today. | `container.py:452-470` |
| `ocr.enabled=False` builds the composite manually with `DigitalTextExtractionAdapter` + `NullOcrExtractor`, and **never imports paddle_table.py / DeskewAdapter**. | `container.py:377-389,428-443` |
| ASGI entrypoint: `reconciliation.infrastructure.api.main:app` (uvicorn :8000). Pipeline runs as a FastAPI **BackgroundTask** triggered by `POST /runs`. | `api/main.py:69-109`, `api/routes.py:11,199` |
| Config: env-first, prefix `RECONCILIATION__`, nested delimiter `__`; api_key env-only (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` injected by validator). | `config.py:108-120,238-246` |
| `frontend/` (vite :5173) is referenced by the root Makefile but is **NOT present in this `feat/rev2-identity-domain` backend checkout**. | `Makefile:18-19`, `Glob frontend/**` → empty |

**Ambiguity resolved (flag #1)** — proposal asks for a `frontend` compose service, but the frontend tree is absent from this branch. **Decision**: scope the frontend service as **optional / deferred** in compose (a commented service block + a `profiles: [ui]` gate), because the e2e verification gate is backend-only (`POST /runs` → reconciliation result). The verification run does not need the review grid. This matches proposal open-question #6 ("backend-only suffices for the e2e gate"). The compose file stays ready for the frontend without blocking on a tree that this branch does not carry.

---

## 1. Architecture approach

**Pattern: Deployment Adapter at the system edge (Hexagonal outer ring).** The container, the
compose file, and the env block are *driving/driven infrastructure* — they configure which
adapters the existing factory wires, and they never enter `domain/`. Nothing in the domain or
`application/pipeline.py` learns that it runs in a container, talks to a cloud model, or
fetches over a network. Three of the five decisions (Dockerfile, cloud-vision wiring, compose)
are **pure packaging + Dependency-Inversion-via-config**: they flip existing config switches
(`ocr.enabled`, `sunat.enabled`, `vision.provider`, `*_crop`) that the `build_pipeline`
factory already honors. Only two decisions touch code, and both are **adapter-internal**:
(a) extend `StampCropConfig`/wire `protocolo_crop` + a consumption-metering hook, (b) add
bounded-concurrency to the SUNAT fetch path. Domain core: **untouched, zero new business
logic** (proposal §2 out-of-scope holds).

```
┌─ container edge (NEW — packaging only) ─────────────────────────────┐
│ Dockerfile(backend)  docker-compose.yml   .env (RECONCILIATION__*)  │
│        │                    │                     │                 │
│        ▼                    ▼                     ▼                 │
│  python:3.12-slim     services: backend     config selection       │
│  uv --frozen (no ml)  PDF ro-mount, run-vol  (Dependency Inversion) │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  build_pipeline(config)  (UNCHANGED factory)
        ┌──────────────────────┼───────────────────────────────┐
        ▼                      ▼                                ▼
  ocr.enabled=false      vision.provider=ollama          sunat.enabled=true
  → NullOcrExtractor      → OpenAICompatibleVisionAdapter  → SunatDescargaqrAdapter
    (no paddle import)      base_url=host Ollama proxy        bounded-concurrency (NEW)
                           model qwen3.5:397b-cloud           cross-run cache (NEW path)
                           ROI crop (stamp + protocolo)
                               │
                               ▼
                    ReconciliationPipeline (domain ports only — PURE, UNCHANGED)
```

---

## 2. Decision records (ADR-style)

### D1 — Backend Dockerfile: slim, paddle-free, uv-frozen, multi-stage

**Decision.** Multi-stage build on **`python:3.12-slim`** (pinned tag, never `latest`), using
**uv** with the committed `uv.lock` and `--frozen`. Install **core + `identity` + `llm`
extras only**; **never** the `ml` extra. Non-root user, `.dockerignore`.

- **Stage 1 (builder)**: `ghcr.io/astral-sh/uv` layer or `pip install uv` on slim; `uv sync
  --frozen --no-dev --extra identity --extra llm` into a venv (or `uv export --frozen --no-dev
  --extra identity --extra llm` → `uv pip install --system`). The set resolves to: fastapi,
  uvicorn[standard], pydantic, pydantic-settings, pymupdf, openpyxl, polars, pyyaml,
  python-multipart (core) + pyzbar, zxing-cpp, Pillow, numpy (identity) + anthropic, openai
  (llm). **Excluded: paddlepaddle, paddleocr** (the only oneDNN/PIR/GPU source).
- **Stage 2 (runtime)**: fresh `python:3.12-slim`, copy the resolved site-packages/venv from
  builder, copy `src/`. Add the **single mandatory system lib** for `pyzbar`: **`libzbar0`**
  (Debian). `zxing-cpp` ships manylinux wheels (self-contained C++, no apt dep). Pillow/numpy
  are manylinux wheels. So runtime apt deps = `libzbar0` only.
- Non-root `appuser` (uid 1000), `WORKDIR /app`, `PYTHONUNBUFFERED=1`.
- `CMD` runs uvicorn `reconciliation.infrastructure.api.main:app --host 0.0.0.0 --port 8000`
  (the real ASGI entrypoint, verified `api/main.py:109`).

**Why.** Excluding the `ml` extra is the entire point: it removes paddle's broken oneDNN/PIR
build and ~800 MB-1 GB of deps, and is *exactly* the dependency boundary the codebase already
draws. `uv --frozen` against a committed lock is the reproducibility guarantee (infra-deploy
"pin the base tag + commit the lock"). Multi-stage keeps the shipped image lean (infra-deploy
mandatory pattern).

**Rejected.**
- `python:3.12` (full) — drags build toolchains the runtime never needs; slim + `libzbar0`
  is enough because zxing/pillow/numpy are wheels.
- `pip install -e .[identity,llm]` without a lock — reintroduces install drift, the exact
  failure this change exists to kill (proposal §1).
- `apt install` for zxing build deps — unnecessary; `zxing-cpp>=2.2` publishes binary wheels.
- Installing the `ml` extra "just in case" — pulls paddle back in; defeats determinism and
  bloats the image (proposal out-of-scope).

**Runtime risk.** If `pyzbar` can't find `libzbar0` at call time it raises on QR decode →
`QrBarcodeExtractionAdapter` import/degrade path returns `identity=None` → scanned guías lose
QR identity + hashqr_url → no SUNAT enrichment. **Mitigation**: `libzbar0` is a hard runtime
apt dep in stage 2, asserted by a build-time smoke (`python -c "import pyzbar.pyzbar"`).

**uv.lock action.** It exists untracked — **commit it** in tasks/apply so the image builds
from a frozen, reviewed lock. If `uv sync --frozen` reveals lock drift vs `pyproject.toml`,
regenerate once with `uv lock`, commit, then build only from the lock thereafter.

---

### D2 — Cloud-vision wiring: container → host Ollama proxy (provider=`ollama`, base_url=host-gateway)

**Decision.** The container reaches the cloud model through the **host's Ollama daemon acting
as a cloud proxy**, not by calling the Ollama cloud API directly from inside the container.

Exact config (env-only, `RECONCILIATION__*`):

```
RECONCILIATION__VISION__PROVIDER=ollama
RECONCILIATION__VISION__OLLAMA__MODEL=qwen3.5:397b-cloud
RECONCILIATION__VISION__OLLAMA__BASE_URL=http://host.docker.internal:11434/v1
# api_key: Ollama path uses the "ollama" placeholder (factory.py:83); the real
# Ollama-cloud bearer token is held HOST-SIDE by the host daemon, never in the container.
```

Compose grants `host.docker.internal` resolution via `extra_hosts: ["host.docker.internal:host-gateway"]`.

**Why `provider=ollama` and not `openai`.** Both map to `OpenAICompatibleVisionAdapter`, but:
- `provider=ollama` sets `supports_batch=False` → the vision stage takes the **sequential
  per-call path** (`pipeline.py:1045-1065`). That is correct: Ollama (local *and* cloud-proxied)
  **does not implement the OpenAI Batch API**. Choosing `provider=openai` would set
  `supports_batch=True`, and the adapter would try `client.batches.create(...)` against an
  endpoint that has no `/batches` → it would except and fall back to sequential anyway
  (`openai_compatible.py:208-216`), but relying on an exception-fallback is fragile and noisy.
  **`provider=ollama` is the honest, intended seam.**
- The `ollama` sub-config already defaults `base_url` and tolerates any api_key
  (`config.py:89-94`, `factory.py:77-84`) — zero new code.

**Why host-proxy over direct cloud API.**
- **Secret containment**: the Ollama-cloud bearer token stays on the host daemon (the
  `llm-infra` skill documents Ollama-cloud Bearer-auth wiring). The container ships **no cloud
  key** — it only knows `http://host.docker.internal:11434/v1` and the placeholder `"ollama"`.
  This satisfies the env-only-secrets + air-gap-by-default invariants: the *container* never
  holds a cloud credential.
- **Single egress point**: all cloud traffic exits through the host's already-authenticated
  Ollama, which the user already runs and has pulled `qwen3.5:397b-cloud` on (engram #2803).
- **No vendor binding in the image**: the container speaks generic OpenAI-compatible HTTP to a
  localhost-shaped URL; swapping back to a fully-local model is a one-line `base_url`/model env
  change.

**Rejected.**
- **Direct Ollama-cloud from container** (`base_url=https://ollama.com/v1`, bearer key in
  container env) — leaks the cloud credential into the container/compose surface and makes the
  image network-coupled to a specific cloud host. Only justified if no host Ollama exists;
  here it does.
- `provider=openai` pointing at OpenAI cloud — different vendor, different cost model, and the
  user's pulled model + plan is Ollama-cloud (engram #2803). Vendor binding the proposal
  forbids.
- `network_mode: host` for the container to reach `localhost:11434` — sacrifices network
  isolation (infra-deploy security rule) for no gain over `host-gateway`.

**Runtime risk.** Wrong `base_url`, host Ollama down, or invalid host-side cloud key →
`OpenAICompatibleVisionAdapter` catches and returns `VisionResult(confidence=0.0)` per call
(it never raises — `openai_compatible.py:181-185`) → every date read is confidence 0.00 →
the reconciliation gate flags those guías for human review (never silent-wrong). **Mitigation**:
a container-start connectivity probe (curl `host.docker.internal:11434/api/tags`) that fails
fast with a clear message before the run, surfaced in the run recipe / metering log.

---

### D3 — ROI consumption optimization: tune `protocolo_crop`, keep `stamp_crop`, add a token-metering hook

**Decision.** Drive cloud token cost down to the floor by sending only tight ROI crops at the
minimum viable resolution, with tight output budget and `<think>`-strip (already present), and
add a **per-call/aggregate consumption-metering hook**.

1. **Guía date stamp** — keep the R7-proven default `stamp_crop=(0.55,0.05,1.0,0.45)`
   (upper-right). No change; it is validated on pages 4,5,6,8,20,25,30 (`config.py:48-64`).
2. **Protocolo "Fecha:" box** — set `protocolo_crop` to the **top-right header table** where
   Registro 232 shows the handwritten "28-05-26". Recommended starting box (to be calibrated):
   **`(x0=0.60, y0=0.04, x1=1.00, y1=0.22)`** — top-right strip, tighter in `y` than the guía
   stamp because the Protocolo "Fecha:" sits in the header table, not a mid-page stamp. This
   replaces today's disabled zero-box (`config.py:102-104`), which forces a full-page ≥300 dpi
   render — the single largest avoidable token cost on the declared side (~35 calls).
   Calibrate against the **R7-proven Protocolo pages** for Registro 232 (and 1-2 more registros)
   before trusting the full run; the box stays env-tunable
   (`RECONCILIATION__VISION__PROTOCOLO_CROP__X0=...`).
3. **Resolution floor** — for cropped calls, render the ROI at the **minimum DPI that keeps the
   handwriting legible to a 397B model**. Design target: start at ~**150-200 dpi on the crop**
   (the crop is ~15-40% of the page, so effective pixels stay modest), and only fall back to
   `fallback_dpi=300` when a crop is *disabled*. Expose the crop render DPI as config so the
   user can bisect cost vs legibility on the cloud plan. (Concretely: the crop DPI lever is the
   measurable knob the user holds the plan to optimize — engram #2803.)
4. **Output budget** — set `max_tokens` **tight** for the cloud reasoning model. The structured
   answer is `{"date":"YYYY-MM-DD","confidence":0..1}` (~20-30 tokens), but qwen3.5 reasoning
   models burn budget on `<think>` first (the comment at `openai_compatible.py:124-129` is why
   the default is 4096). **Decision**: keep a moderate ceiling (e.g. **512-768**) — high enough
   to survive the think-phase and still emit JSON, low enough to bound a runaway. Pair with a
   `/no_think`-style suppression where the OpenAI-compat endpoint honors it (`extra_body`),
   noted as a tuning lever, not a hard dependency (the `<think>` strip already makes output
   parse-safe).
5. **`<think>` strip** — already implemented (`_THINK_RE`, `openai_compatible.py:46-65`). Reuse
   as-is (R7 fix). No change.
6. **Metering hook** — add a lightweight **consumption meter** at the adapter boundary
   (`OpenAICompatibleVisionAdapter`): record per-call `usage.prompt_tokens` /
   `usage.completion_tokens` from the response (OpenAI-compatible responses carry `usage`), log
   per call and accumulate a per-run aggregate, surfaced at run end. This is the measurement
   surface the proposal/engram #2803 explicitly want ("the user has an Ollama cloud plan to
   measure/optimize consumption"). **Keep it pure-adapter**: a counter + structured log line; it
   feeds nothing in the domain. Optionally gate the run with the existing
   `vision.max_vision_calls` cap (already enforced, `pipeline.py:1000-1056`) — that bounds
   *call count*; the meter bounds *tokens*.

**Pattern.** Region-of-Interest extraction (send the smallest sufficient pixels), plus an
edge-side instrumentation hook (Decorator-style accounting around the adapter call).

**Why.** Each of the ~70 calls (35 guía + 35 Protocolo) against a 397B cloud model costs
tokens proportional to image pixels + output. Full-page 300 dpi vs a 150 dpi top-right crop is
an order-of-magnitude pixel difference. The declared side (`protocolo_crop`) is currently the
worst offender because it is disabled → full page. Fixing it is the highest-ROI lever.

**Rejected.**
- Leaving `protocolo_crop` disabled — proposal calls this out as the missing piece; full-page
  declared reads dominate cost.
- Sending full pages "for safety" — defeats the cloud-plan purpose; mitigated instead by
  reconciliation flagging wrong reads for human review, not by spending tokens.
- Hard-binding `max_tokens` very low (e.g. 64) — the think-phase eats it → empty content →
  confidence 0.00 (the exact R7 empty-content bug). The 512-768 floor avoids that.

**Runtime risk.** A too-tight crop clips the handwritten date → low/zero confidence → flagged
for human review (safe, never silent-wrong). **Mitigation**: calibrate on known pages first
(D6); crop box + crop DPI are env-tunable for bisection.

---

### D4 — SUNAT fetch: bounded-concurrency parallel fetch + cross-run on-disk cache

**Decision.** Replace the strictly-sequential SUNAT loop with **bounded-concurrency parallel
fetch** (asyncio + semaphore of N) **and** make the on-disk cache **reusable across runs**.
Both changes are **adapter-/stage-internal**; the domain and ports are untouched.

1. **Bounded concurrency (Semaphore pattern).** Today `_stage_sunat_fetch` calls
   `self._sunat.fetch(url)` sequentially for ~35 blocks; with the 0.5 s pacing + 60 s read
   budget + backoff, that is the observed 20-30 min wall-clock (proposal §1). Design: fetch the
   blocks concurrently with an **`asyncio.Semaphore(N)`** bound (`N≈4-6`), so at most N requests
   are in flight against SUNAT at once. This caps load to stay under SUNAT rate-limiting while
   collapsing wall-clock from `35×(read+pace)` to roughly `ceil(35/N)×(read+pace)` — minutes,
   not half-hours.
   - **Implementation locus (keep domain pure)**: the parallelism lives **inside the SUNAT
     adapter** as a new `fetch_many(urls) -> dict[url, OfficialGre|None]` (or an async
     `afetch`), and `_stage_sunat_fetch` calls that batch method instead of looping. The
     pipeline stage stays a thin orchestrator; the domain ports (`SunatGreFetchPort`) gain at
     most one batch method (additive, optional) — no business logic.
   - `httpx` already supports async (`httpx.AsyncClient`); the adapter currently uses the sync
     `httpx.get`. The async path is an adapter-internal addition, lazy-imported like the rest.
   - **Pacing under concurrency**: the existing `_FETCH_PACING_S` (a per-instance
     last-download timestamp) is a *sequential* pacer; under N-concurrency it must become a
     **semaphore bound + optional per-slot jitter**, not a global serializing sleep (otherwise
     concurrency buys nothing). The semaphore *is* the rate limiter.
   - **429 handling**: keep the existing retry+backoff; treat HTTP 429 as a backoff trigger
     (exponential) and, on repeated 429, **shrink effective N** (back-pressure). Graceful-None
     contract is preserved — a permanently failing fetch returns None and the block keeps OCR
     lines (here: keeps nothing, since OCR is null → that guía simply has no SUNAT quantities
     and reconciles as-is / flags).

2. **Cross-run cache (Cache-aside).** Today cache_dir = `ctx.run_dir/"sunat"` → discarded per
   run (`container.py:460`). Design: point the cache at a **stable, mounted volume path**
   (e.g. `/data/sunat-cache`, env-overridable) so a re-run reuses already-downloaded GRE PDFs
   and makes **zero** network calls for cache hits. The adapter's cache-aside logic already
   keys on the hashqr token (`_url_to_cache_key`, `descargaqr.py:405-422`) and is
   run-independent — only the *directory* needs to move out of the per-run dir.
   - **Decision**: add a `SunatConfig.cache_dir: Path | None` (or reuse an env override) so the
     container mounts a persistent cache volume; default stays per-run when unset (no behavior
     change outside the container). This keeps per-run **output** isolation (the proposal
     invariant) while sharing only the immutable, content-addressed SUNAT PDFs.

**Combined effect.** First faithful run: ~`ceil(35/N)` concurrent waves → minutes. Every
subsequent run: near-instant (cache hits, no egress). This is a **verification-time throughput**
fix, not a correctness change — SUNAT data and the graceful-None contract are unchanged.

**Pattern.** Bounded-Concurrency (counting semaphore) + Cache-aside, both isolated to the
driven adapter (Ports & Adapters: the port stays a behavior contract; the adapter changes how
it fulfills it).

**Rejected.**
- **Unbounded `asyncio.gather`** over 35 URLs — would burst SUNAT and trip rate-limiting /
  bans; the whole point is *bounded* concurrency.
- **Cache-only, no concurrency** — solves *re-runs* but the *first faithful run* still takes
  20-30 min sequentially. The proposal wants the faithful run itself in minutes → need both.
- **Threadpool over sync httpx** — works, but the codebase is moving stage IO toward explicit
  bounds; an async semaphore expresses the rate-limit intent precisely and `httpx.AsyncClient`
  is already a dep. Either is acceptable; asyncio-semaphore is the recommended primary.
- Touching the domain to parallelize — forbidden; concurrency is an adapter concern.

**Runtime risk.** Too-high N → SUNAT 429/ban → fetches return None → guías lose SUNAT
quantities. **Mitigation**: conservative N (4-6), 429-triggered backoff + N-shrink, cache so a
partial run's successes persist and the next run only retries the misses.

---

### D5 — Compose: backend service, read-only PDF mount, per-run output volume, cache volume, frontend deferred

**Decision.** A `docker-compose.yml` with a **`backend`** service (the verification target),
an **optional `frontend`** service behind a `profiles: [ui]` gate (deferred — tree absent on
this branch, see ambiguity #1), and a dedicated **run target**.

```yaml
services:
  backend:
    build: { context: ./backend, dockerfile: Dockerfile }
    extra_hosts: ["host.docker.internal:host-gateway"]   # D2 cloud-vision reach
    environment:
      RECONCILIATION__OCR__ENABLED: "false"               # NullOcrExtractor, no paddle
      RECONCILIATION__VISION__PROVIDER: "ollama"
      RECONCILIATION__VISION__OLLAMA__MODEL: "qwen3.5:397b-cloud"
      RECONCILIATION__VISION__OLLAMA__BASE_URL: "http://host.docker.internal:11434/v1"
      RECONCILIATION__VISION__PROTOCOLO_CROP__X0: "0.60"   # D3 (and Y0/X1/Y1)
      RECONCILIATION__SUNAT__ENABLED: "true"               # only egress, opt-in
      RECONCILIATION__SUNAT__CACHE: "true"
      RECONCILIATION__OUTPUT_DIR: "/data/runs"
    volumes:
      - ./input/CTR-PLC01-FR001.pdf:/data/input.pdf:ro     # real PDF, READ-ONLY
      - run-output:/data/runs                              # per-run isolation preserved
      - sunat-cache:/data/sunat-cache                      # D4 cross-run cache
    # default: serve API (uvicorn :8000) for POST /runs; OR see run target below
  # frontend:                       # DEFERRED — profiles: [ui]; tree not on this branch
volumes: { run-output: , sunat-cache: }
```

**How the verification run is invoked.** Two supported shapes, recommend **(a)**:

(a) **Make target + compose** (recommended): `make verify` →
`docker compose up -d backend` then `curl -X POST :8000/runs` with the mounted PDF path, poll
`GET /runs/{id}` to completion, then pull the result/xlsx from the `run-output` volume. This
exercises the **real service path** (the BackgroundTask pipeline, `routes.py:199`), which is
what production actually runs — highest-fidelity verification.

(b) **`docker compose run --rm backend <oneshot>`**: a thin module entrypoint that calls
`build_pipeline(config)` + `pipeline.run(ctx)` directly and exits. Lower overhead, but it
bypasses the API/BackgroundTask layer. Acceptable as a CI-style gate; offer it as
`make verify-oneshot`.

**Why.** Compose, not K8s/K3s — two services, one machine, no autoscaling (infra-deploy
decision tree; proposal §3). Read-only PDF mount + named output volume preserves the
"input is read-only, each run isolated" domain invariant (CLAUDE.md). `extra_hosts:
host-gateway` is the documented mechanism for container→host reach on Docker. Secrets stay
env/host-side (no cloud key in compose — D2). Frontend deferred behind a profile keeps the file
ready without coupling the gate to an absent tree.

**Rejected.**
- Mounting the PDF read-write — violates the read-only-input invariant; no reason to.
- Baking the PDF into the image — it is large, machine-specific, and read-only input belongs in
  a mount, not a layer.
- `network_mode: host` — breaks isolation; `host-gateway` suffices (D2).
- A mandatory frontend service — would fail to build on this branch (no tree); deferred via
  profile.

**Runtime risk.** Missing `host-gateway` support (very old Docker) → `host.docker.internal`
unresolved → vision calls fail → confidence 0.00 → flagged. **Mitigation**: documented Docker
version floor + the D2 start-up connectivity probe.

---

### D6 — Cloud-model accuracy smoke (design-time validation gate, before the full run)

**Decision.** Before trusting a full ~70-call run, run a **single-page accuracy smoke**: feed
one Protocolo page (Registro 232, known handwritten "28-05-26") through the cloud
`qwen3.5:397b-cloud` path with the tuned `protocolo_crop`, and assert it reads `2026-05-28`.
Compare against the **local `qwen3.5:9b` ground truth** already validated on the R7-proven
pages (4,5,6,8,20,25,30). Repeat for one guía stamp page.

- **Mechanism**: a small, opt-in smoke (a marked `e2e`/`slow` test or a `make smoke` target)
  that builds the vision adapter from the cloud config and calls `read_handwritten_date` on the
  cropped known page — reusing the *exact* production adapter + crop path, not a mock.
- **Pass criterion**: cloud read date == ground-truth date for the calibration pages, at
  confidence ≥ the 0.85 gate. If the 397B model misreads where 9b succeeded, **do not run the
  full 70 calls** — re-tune the crop/DPI (D3) or fall back to local vision for the declared
  side.
- **Output**: the smoke also exercises the D3 metering hook → first real token-per-call
  measurement on the cloud plan, feeding the consumption-optimization loop before committing to
  the full run.

**Why.** The proposal's top accuracy risk is that the 397B cloud model reads handwriting
differently from the locally-validated 9b (proposal §4). The reconciliation gate is the
*safety net* (mismatches flag, never auto-correct), but a calibration smoke is the *cheap
pre-check* that avoids spending ~70 cloud calls on a miscalibrated crop. It is design-time
validation against known ground truth — the "real-data e2e before trusting green" working
agreement (CLAUDE.md), applied to the cloud model.

**Rejected.**
- Trusting the full run directly because reconciliation flags mismatches — wastes tokens and
  conflates "model can't read this crop" with "data genuinely diverges". Calibrate first.
- Mocking the cloud response in the smoke — defeats the purpose; the smoke must hit the real
  cloud path to measure real accuracy + real tokens.

**Runtime risk.** Smoke passes on Registro 232 but the full set has harder handwriting →
some reads flag. **Mitigation**: that is the *intended* behavior of the reconciliation gate
(human review); the smoke only guarantees the crop/DPI/model pipeline is sound, not that every
hand is legible.

---

## 3. Component & data-flow map

```
docker compose up backend
  │
  ├─ env (RECONCILIATION__*) ──► AppConfig (config.py, env-first)
  │
  ├─ build_pipeline(config)  (container.py — UNCHANGED factory, Dependency Inversion)
  │     ├─ ocr.enabled=false ─► CompositeExtractor[DigitalText + NullOcr]   (no paddle)
  │     ├─ vision.provider=ollama ─► OpenAICompatibleVisionAdapter
  │     │        base_url=http://host.docker.internal:11434/v1, model=qwen3.5:397b-cloud
  │     │        + ROI crop (stamp_crop / protocolo_crop) + token meter (D3)
  │     ├─ sunat.enabled=true ─► SunatDescargaqrAdapter
  │     │        + bounded-concurrency fetch_many (D4) + cross-run cache volume
  │     └─ identity ─► QrBarcodeExtractionAdapter (needs libzbar0 — D1)
  │
  ├─ POST /runs ─► BackgroundTask ─► pipeline.run(ctx)   (domain ports only — PURE)
  │     stage classify → SUNAT fetch_many → vision (guía dates) →
  │     declared date (protocolo_crop) → reconcile (EXACT, flag<0.85) → export
  │
  └─ results ─► /data/runs/{run_id}/  (named volume, per-run isolation)

host side:  Ollama daemon :11434  ──(bearer key, host-only)──►  qwen3.5:397b-cloud
egress:     container ──► e-factura.sunat.gob.pe (SUNAT, opt-in, bounded N)
                container ──► host.docker.internal (Ollama proxy → cloud)
```

**Integration points (edges only):**
1. Container → host Ollama (`host.docker.internal:11434/v1`) — cloud vision proxy (D2).
2. Container → SUNAT (`e-factura.sunat.gob.pe`) — bounded-concurrency egress (D4), opt-in.
3. Host → read-only PDF mount; container → run-output + sunat-cache volumes (D5).
4. Env → AppConfig → existing factory (Dependency Inversion; no new wiring) (D1/D2/D5).

**Touched code (all edge/adapter, domain PURE):**
- `backend/Dockerfile`, `.dockerignore`, `docker-compose.yml`, `Makefile` (new edge artifacts).
- `uv.lock` (commit existing).
- `config.py`: `protocolo_crop` real box default (or env-set), optional crop-DPI field,
  optional `SunatConfig.cache_dir`.
- `adapters/vision/openai_compatible.py`: token-metering hook (additive, adapter-internal).
- `adapters/sunat/descargaqr.py` + `pipeline.py::_stage_sunat_fetch`: bounded-concurrency
  `fetch_many` (additive; stage calls batch method).
- docs: DECISIONS/HANDOFF — cloud+SUNAT egress as recorded opt-in deviation.

---

## 4. Invariants preserved (cross-check)

| Invariant (CLAUDE.md / proposal) | How design holds it |
|---|---|
| Domain core PURE — no SDK/IO import | All changes at container edge or inside existing adapters; `application/pipeline.py` still depends only on ports. Zero new domain logic. |
| Provider-agnostic vision (no vendor binding) | Rides existing `VisionLLMPort` factory; `provider=ollama` + `base_url` env swap; container ships no vendor key. |
| Units KG/TN/RD/Rollo summed, never converted | Untouched — no domain/grouping change. |
| MATCH EXACT(0), flag<0.85, reconciliation is the gate | Untouched; cloud misreads flag for human review, never auto-correct. |
| Reception `fecha` = handwritten (vision) | `protocolo_crop` (declared) + `stamp_crop` (guía) both read handwriting via the same port; year never trusted (existing). |
| Input PDF read-only; per-run isolated output | RO bind-mount + named `run-output` volume; only the immutable SUNAT cache is shared. |
| Local-first / air-gap is the default | Cloud vision + SUNAT are opt-in env flags; defaults stay off; deviation recorded in DECISIONS. |
| Adapters lazy-import heavy deps | Preserved; async httpx + openai still lazy-imported inside methods. |

---

## 5. Open items handed to `sdd-tasks`

- Exact `protocolo_crop` box: ship `(0.60,0.04,1.0,0.22)` as the starting default, calibrate on
  Registro 232 in D6 before the full run; keep env-tunable.
- Concurrency bound `N` for SUNAT (start 4-6) and whether `fetch_many` is async-httpx or
  threadpool — recommend async semaphore; confirm in apply.
- Crop render DPI field name + default (start 150-200 on the crop).
- `max_tokens` final value (start 512-768) and whether to wire a `/no_think` `extra_body` lever.
- Token-meter surface: log line + per-run aggregate; optional ceiling.
- `SunatConfig.cache_dir` field vs pure env override for the cross-run cache volume path.
- Frontend service: keep deferred (`profiles: [ui]`) or drop until the tree lands on this branch.
- Image-size / build-time budget check (no-paddle target: expect a few hundred MB).
```
