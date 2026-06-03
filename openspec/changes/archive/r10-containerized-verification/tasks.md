# Tasks — r10-containerized-verification

**Change**: `r10-containerized-verification` · **Phase**: tasks · **Store**: hybrid · **Date**: 2026-06-02
**Branch**: `feat/rev2-identity-domain` (continuing; no new branch)
**Strict TDD**: active for code changes — `cd backend && uv run pytest tests/unit/<dir>` PER-DIRECTORY.
Infra artifacts (Dockerfile, compose) are validated by build + smoke, not unit tests.

Covers CONT-001 through CONT-008. Domain core is PURE — every change lands at the system edge
(Dockerfile, compose, config, two adapter-internal additions). Architecture pattern: Deployment
Adapter at the system edge + Dependency-Inversion-via-config + Bounded-Concurrency + Cache-aside.

All code tasks are ordered **test-first** per strict TDD mode. Tests and code ship in the SAME
commit (work-unit-commits skill). Each task = one work-unit commit with a clear start/finish and
a rollback boundary that does not remove unrelated work.

Task types:
- `[INFRA]` — Dockerfile / compose / build / Makefile artifacts; validated by build + smoke
- `[CODE]`  — adapter-internal Python; TDD (unit tests first, per-directory runner)
- `[EMPIRICAL]` — smoke calibration / accuracy gate / full faithful run; requires real PDF + cloud

---

## Review Workload Forecast

| Metric | Estimate |
|--------|----------|
| New files | 3 (Dockerfile, docker-compose.yml, .dockerignore) + Makefile target(s) |
| Modified files | 5 (config.py, openai_compatible.py, descargaqr.py, pipeline.py, container.py) + uv.lock commit |
| Estimated changed lines | ~280–360 (code only; infra files are ~80 lines each, not counted in code budget) |
| 400-line budget risk | **Low-Medium** — code changes stay well under 400 lines; infra files add bulk but are non-risky config text, not logic. |
| Chained PRs recommended | Not blocking — all on the unpushed feature branch; user gates PR submission. Work-unit commits are independently reviewable. |
| Decision needed before apply | No — proceed with work-unit commits on `feat/rev2-identity-domain`. |

---

## Task Dependency Graph

```
R10.1 (uv.lock commit — reproducible pin)
  └─▶ R10.2 (Dockerfile — paddle-free multi-stage, uv --frozen)
        └─▶ R10.3 (.dockerignore + docker-compose.yml — PDF mount, volumes, host-gateway)
              └─▶ R10.4 (Makefile: make build / make verify / make smoke targets)
                    └─▶ R10.5 (config.py: protocolo_crop default + crop_dpi + SunatConfig.cache_dir)
                          ├─▶ R10.6 (openai_compatible.py: token-metering hook — TDD)
                          └─▶ R10.7 (descargaqr.py + pipeline.py: fetch_many bounded-concurrency — TDD)
                                └─▶ R10.8 (container.py: wire SunatConfig.cache_dir to stable volume path)
                                      └─▶ R10.9 [EMPIRICAL] (cloud-vision accuracy smoke + crop calibration — MUST PASS before R10.10)
                                            └─▶ R10.10 [EMPIRICAL] (faithful in-container full run: R8 gate + R9 gate)
                                                  └─▶ R10.11 (docs: DECISIONS.md + HANDOFF.md — cloud egress + SUNAT deviation)

Parallel opportunities:
  R10.6 and R10.7 are parallel once R10.5 is done (independent adapter files)
  R10.8 depends on R10.7 (uses the new fetch_many signature) but NOT on R10.6
  R10.9 requires R10.4 (image builds) and R10.6 (meter hook) and R10.5 (crop config) — fan-in
```

---

## Slice R10-A — Reproducibility Foundation

> Sequential. Sets the lockfile baseline that every subsequent build depends on.

### [x] R10.1 — Commit `uv.lock` (reproducible dependency pin)

**Type**: `[INFRA]`
**Spec refs**: CONT-001 (pinned lockfile), CONT-S01 (builds identically on any machine).
**Depends on**: nothing — foundation for everything else.
**Parallel with**: nothing.

**Deliverables**:
- Verify `uv.lock` is present at `backend/uv.lock` (the design confirms it exists, untracked).
  If the file is at the repo root instead of `backend/`, confirm the path relative to where
  `pyproject.toml` lives; that is the canonical location for `uv`.
- Run `uv lock --check` (inside `backend/`) to assert the lockfile is in sync with `pyproject.toml`.
  If drift is detected, run `uv lock` once to regenerate, then inspect the diff for surprises.
- `git add uv.lock && git commit`.
- **No tests** — this is a data file; the Dockerfile build in R10.2 acts as the integration proof.

**Commit message**: `chore(deps): commit uv.lock for reproducible pinned builds (CONT-001)`
**Completable in**: one session (< 5 min if no drift; ~15 min if regeneration needed).

---

## Slice R10-B — Container Image + Compose

> Sequential within slice. Each artifact builds on the previous one.

### [x] R10.2 — `backend/Dockerfile` — paddle-free multi-stage image

**Type**: `[INFRA]`
**Spec refs**: CONT-001 (no paddle, pinned), CONT-S01 (build identical), CONT-S02 (paddle import absent), CONT-S03 (tests pass inside container).
**Depends on**: R10.1 (uv.lock committed so `--frozen` resolves cleanly).
**Parallel with**: nothing at this step.

**Deliverables** (new file `backend/Dockerfile`):

Stage 1 — builder:
```
FROM python:3.12-slim AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra identity --extra llm
```
(Exact invocation subject to uv image tag available; alternative is `pip install uv` then
`uv sync`. The key constraint: `--frozen`, no `--extra ml`, no `--extra dev`.)

Stage 2 — runtime:
```
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY src/ ./src/
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
CMD ["uvicorn", "reconciliation.infrastructure.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build-time smoke baked into the Dockerfile (RUN layer before USER switch):
```dockerfile
RUN python -c "import pyzbar.pyzbar; import zxing; print('identity ok')" && \
    python -c "from openai import OpenAI; print('llm ok')" && \
    python -c "import paddle" 2>&1 | grep -q "ModuleNotFoundError" && echo "paddle absent — CONT-S02 OK"
```
(If paddle is NOT absent, the build fails here — the RUN layer exits non-zero. This is the
paddle-free assertion baked into CI.)

**Validation** (after writing the file, not a unit test):
- `docker build -t ctr-backend:smoke ./backend` — must exit 0.
- `docker run --rm ctr-backend:smoke python -c "import paddle"` — must exit non-zero
  (ModuleNotFoundError → CONT-S02 verified).

**Commit message**: `feat(infra): add paddle-free multi-stage Dockerfile with uv --frozen (CONT-001)`
**Completable in**: one session (~40-60 min including build verification).

---

### [x] R10.3 — `.dockerignore` + `docker-compose.yml`

**Type**: `[INFRA]`
**Spec refs**: CONT-001 (image lean), CONT-S03 (tests in container), CONT-S04 (cloud vision config), CONT-S06 (ocr.enabled=false), CONT-S09 (SUNAT parallel), CONT-S10 (cache across runs), CONT-S14/S15 (air-gap default preserved; cloud is opt-in profile).
**Depends on**: R10.2 (Dockerfile exists and builds).
**Parallel with**: nothing.

**Deliverables** (new file `backend/.dockerignore`):
```
.venv/
__pycache__/
*.pyc
.env
tests/
*.egg-info
.git
```

**Deliverables** (new file `docker-compose.yml` at repo root alongside `backend/`):
```yaml
services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      RECONCILIATION__OCR__ENABLED: "false"
      RECONCILIATION__VISION__PROVIDER: "ollama"
      RECONCILIATION__VISION__OLLAMA__MODEL: "qwen3.5:397b-cloud"
      RECONCILIATION__VISION__OLLAMA__BASE_URL: "http://host.docker.internal:11434/v1"
      RECONCILIATION__VISION__MAX_TOKENS: "640"
      RECONCILIATION__VISION__PROTOCOLO_CROP__X0: "0.60"
      RECONCILIATION__VISION__PROTOCOLO_CROP__Y0: "0.04"
      RECONCILIATION__VISION__PROTOCOLO_CROP__X1: "1.00"
      RECONCILIATION__VISION__PROTOCOLO_CROP__Y1: "0.22"
      RECONCILIATION__SUNAT__ENABLED: "true"
      RECONCILIATION__SUNAT__CACHE: "true"
      RECONCILIATION__SUNAT__CACHE_DIR: "/data/sunat-cache"
      RECONCILIATION__OUTPUT_DIR: "/data/runs"
    volumes:
      - ./input/CTR-PLC01-FR001.pdf:/data/input.pdf:ro
      - run-output:/data/runs
      - sunat-cache:/data/sunat-cache

  # frontend: deferred — profiles: [ui]; frontend tree absent from this branch
  # Uncomment when frontend/ lands on this branch.
  # frontend:
  #   profiles: [ui]
  #   build: { context: ./frontend }
  #   ports: ["5173:5173"]

volumes:
  run-output:
  sunat-cache:
```

**Notes on env design**:
- `RECONCILIATION__VISION__MAX_TOKENS` surfaces the 512-768 ceiling as an env variable.
  Requires R10.5 to wire a `max_tokens` field onto `VisionConfig` (currently hardcoded
  in the adapter constructor at `openai_compatible.py:141`). If R10.5 does not add this
  field, set it directly in the Dockerfile ENV or accept the current adapter default.
  Dependency: ensure compose env map is consistent with what config.py reads after R10.5.
- `RECONCILIATION__SUNAT__CACHE_DIR` requires the `cache_dir` field added in R10.5/R10.8.
  The env key is declared here but the code to read it lands in R10.5+R10.8.

**Validation**: `docker compose config` — must print the resolved config without errors.

**Commit message**: `feat(infra): add .dockerignore and docker-compose.yml with cloud-vision + SUNAT config (CONT-001/D2/D5)`
**Completable in**: one session (~30 min).

---

### [x] R10.4 — `Makefile` targets: `build`, `verify`, `smoke`, `test-container`

**Type**: `[INFRA]`
**Spec refs**: CONT-S03 (test suite runs in container), CONT-S12/S13 (faithful in-container run).
**Depends on**: R10.3 (compose file exists).
**Parallel with**: nothing.

**Deliverables** (add to root `Makefile`):
```makefile
# ─── r10 Container verification targets ───────────────────────────────────────

## Build the backend image (paddle-free, uv --frozen)
.PHONY: build
build:
	docker compose build backend

## Run the full backend unit test suite INSIDE the container (CONT-S03)
.PHONY: test-container
test-container:
	docker compose run --rm backend \
	  bash -c "cd /app && python -m pytest tests/unit/domain tests/unit/application tests/unit/adapters tests/unit/infrastructure -v --tb=short"

## Cloud-vision accuracy smoke: one Protocolo page → qwen3.5:397b-cloud (CONT-S07/S08)
## Run BEFORE the full verification to calibrate crop + measure tokens.
.PHONY: smoke
smoke:
	docker compose run --rm backend \
	  python -m pytest tests/e2e/test_smoke_cloud_vision.py -v -s --tb=short

## Full faithful in-container verification run: POST /runs → poll → assert R8+R9 gates
## Requires: make build, Ollama host daemon running with qwen3.5:397b-cloud pulled.
.PHONY: verify
verify:
	docker compose up -d backend
	@echo "Waiting for backend to be healthy..."
	@sleep 5
	docker compose run --rm backend \
	  python -m pytest tests/e2e/test_container_verification.py -v -s --tb=short
	docker compose down
```

**Note on e2e test paths**: `tests/e2e/` does not exist yet — it is created in R10.9 and R10.10.
The Makefile targets are declared here so the recipe is committed alongside the infra artifacts;
the test files they invoke land in later tasks.

**Commit message**: `feat(infra): add Makefile build/test-container/smoke/verify targets (CONT-S03/S12/S13)`
**Completable in**: one session (< 20 min).

---

## Slice R10-C — Config Extensions

> Single task. Prerequisite for all code changes in slice R10-D.

### [x] R10.5 — `config.py`: `protocolo_crop` default, `max_tokens`, `SunatConfig.cache_dir`

**Type**: `[CODE]` (TDD)
**Spec refs**: CONT-003 (cloud vision config-only), CONT-005 (ROI crop sent, not full page), CONT-006 (cross-run cache), CONT-S04, CONT-S07, CONT-S10.
**Depends on**: R10.4 (compose file declares the env keys; this task makes config.py read them).
**Parallel with**: nothing — R10.6 and R10.7 both read from config.py.

**Deliverables** (modify `backend/src/reconciliation/application/config.py`):

1. **`protocolo_crop` starting default** — change the current disabled zero-box default to the
   calibration starting point:
   ```python
   protocolo_crop: StampCropConfig = Field(
       default_factory=lambda: StampCropConfig(x0=0.60, y0=0.04, x1=1.00, y1=0.22)
   )
   ```
   This box is env-tunable (`RECONCILIATION__VISION__PROTOCOLO_CROP__X0` etc.) and will be
   empirically calibrated in R10.9. Replacing the zero-box default is the primary declared-side
   token-cost lever (D3).

2. **`VisionConfig.max_tokens`** — expose the adapter's `max_tokens` parameter as a config field
   so it is env-tunable without rebuilding:
   ```python
   max_tokens: int = Field(default=640, gt=0)
   ```
   640 sits within the 512-768 design range. The factory passes this to
   `OpenAICompatibleVisionAdapter(max_tokens=config.vision.max_tokens, ...)`. Currently the
   adapter is constructed with its own `max_tokens=4096` default (`openai_compatible.py:141`);
   this change routes the config value into the constructor call in `container.py`.
   **Action in `container.py`** (small, at the vision-adapter construction site): pass
   `max_tokens=config.vision.max_tokens`.

3. **`SunatConfig.cache_dir`** — add an optional stable cache path:
   ```python
   cache_dir: Path | None = Field(default=None)
   ```
   When set, `container.py` (R10.8) uses it instead of the per-run dir. Default `None` keeps
   existing behavior (per-run cache, no cross-run reuse) — backward-compatible.

**Tests** (update `backend/tests/unit/application/test_config.py`):
- Default `AppConfig` has `vision.protocolo_crop` with `x0=0.60`, `y0=0.04`, `x1=1.00`, `y1=0.22`.
- `protocolo_crop.enabled` is `True` for the new default (non-degenerate box).
- `vision.max_tokens` defaults to 640.
- `AppConfig` with `RECONCILIATION__VISION__MAX_TOKENS=512` env → `vision.max_tokens == 512`.
- `SunatConfig` default `cache_dir` is `None`.
- `AppConfig` with `RECONCILIATION__SUNAT__CACHE_DIR=/data/sunat-cache` → `sunat.cache_dir == Path("/data/sunat-cache")`.
- Old config.yaml without `cache_dir` key → `model_validate` succeeds with `None` default (backward-compat).

**Test runner**: `cd backend && uv run pytest tests/unit/application/ -v --tb=short`

**Commit message**: `feat(config): set protocolo_crop default, add max_tokens + SunatConfig.cache_dir (CONT-003/005/006)`
**Completable in**: one session (small — ~25 lines production, ~30 lines tests).

---

## Slice R10-D — Adapter Code Changes (Parallel)

> R10.6 and R10.7 are parallel once R10.5 is complete. R10.8 depends on R10.7 only.

### [x] R10.6 — `openai_compatible.py`: token-consumption metering hook

**Type**: `[CODE]` (TDD)
**Spec refs**: CONT-005 (consumption observable), CONT-S08 (token count logged per call and aggregate).
**Depends on**: R10.5 (`vision.max_tokens` wired into the adapter constructor).
**Parallel with**: R10.7 (independent file).

**Deliverables** (modify `backend/src/reconciliation/adapters/vision/openai_compatible.py`):

1. Add a `_TokenMeter` dataclass (module-level, stdlib only — no new imports):
   ```python
   @dataclasses.dataclass
   class _TokenMeter:
       prompt_tokens: int = 0
       completion_tokens: int = 0
       calls: int = 0

       def record(self, usage: object | None) -> None:
           if usage is None:
               return
           self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
           self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
           self.calls += 1

       @property
       def total_tokens(self) -> int:
           return self.prompt_tokens + self.completion_tokens
   ```

2. Add `self._meter = _TokenMeter()` to `__init__`.

3. In `read_handwritten_date`, after `response = client.chat.completions.create(...)`, add:
   ```python
   self._meter.record(getattr(response, "usage", None))
   logger.debug(
       "vision meter: call=%d prompt=%d completion=%d total=%d (model=%s)",
       self._meter.calls,
       self._meter.prompt_tokens,
       self._meter.completion_tokens,
       self._meter.total_tokens,
       self._model,
   )
   ```

4. Add a public read-only property `meter: _TokenMeter` so callers (pipeline, tests) can
   inspect the aggregate without mutating it.

5. In `pipeline.py`, after `_stage_extract_vision` completes, log the aggregate meter from
   the vision adapter if it exposes `.meter`:
   ```python
   if hasattr(self._vision, "meter"):
       m = self._vision.meter
       logger.info(
           "vision aggregate: calls=%d prompt=%d completion=%d total=%d",
           m.calls, m.prompt_tokens, m.completion_tokens, m.total_tokens,
       )
   ```
   This is **one additive log call** in `pipeline.py` — it does not change any stage logic
   and is the minimal surface that satisfies CONT-S08 without coupling the domain to the meter.

**Tests** (new file `backend/tests/unit/adapters/test_vision_meter.py`):
- Instantiate `OpenAICompatibleVisionAdapter` with an injected mock client that returns a
  response with `usage.prompt_tokens=150, usage.completion_tokens=30`.
- After one `read_handwritten_date` call: `adapter.meter.calls == 1`,
  `adapter.meter.prompt_tokens == 150`, `adapter.meter.completion_tokens == 30`,
  `adapter.meter.total_tokens == 180`.
- After two calls: `meter.calls == 2`, `total_tokens == 360` (accumulated, not reset).
- Response with `usage=None` → meter still increments `calls` by 0 (graceful — only record
  when usage is not None).
- Response with missing `prompt_tokens` attribute on usage → falls back to 0; no crash.

**Test runner**: `cd backend && uv run pytest tests/unit/adapters/ -v --tb=short`

**Commit message**: `feat(adapters): add token-consumption metering hook to OpenAICompatibleVisionAdapter (CONT-S08)`
**Completable in**: one session (small — ~40 lines production, ~30 lines tests).

---

### [x] R10.7 — `descargaqr.py` + `pipeline.py`: bounded-concurrency `fetch_many`

**Type**: `[CODE]` (TDD)
**Spec refs**: CONT-006 (bounded parallelism, 429 backoff, no crash), CONT-S09 (parallel faster than sequential), CONT-S11 (429 backoff no crash).
**Depends on**: R10.5 (config stable — no config dependency in this task itself, but keeps the slice ordered).
**Parallel with**: R10.6 (independent adapter file).

**Deliverables** (modify `backend/src/reconciliation/adapters/sunat/descargaqr.py`):

1. Add `async def fetch_many(self, urls: list[str], concurrency: int = 5) -> dict[str, OfficialGre | None]`:
   - Uses `asyncio.Semaphore(concurrency)` to bound in-flight requests.
   - Replaces per-instance `_FETCH_PACING_S` sequential sleep with semaphore as the rate limiter
     (the sequential pacing is irrelevant under concurrency — the semaphore takes its role).
   - Each slot calls `self.fetch(url)` (existing sync method) via `asyncio.to_thread` to avoid
     blocking the event loop. Alternative: extract an async inner fetch; `to_thread` is simpler
     and keeps the sync `fetch()` path working for existing code and tests.
   - 429 handling: if `fetch()` returns `None` (the existing graceful-None contract catches
     HTTP 429 already via retry+backoff in `_fetch_internal`), respect the None and continue.
     For N-shrink: after 3 consecutive `None` results that the caller can detect as 429-origin,
     reduce `concurrency` by 1 (minimum 1). Implementation note: the existing adapter already
     wraps retries internally — N-shrink is a best-effort guard here, not a hard requirement;
     the semaphore bound is the primary protection.
   - Returns `dict[url, OfficialGre | None]` — same graceful-None contract as `fetch()`.
   - Lazy-import `asyncio` inside the method body (already stdlib; no new dep, but keep lazy
     pattern consistent with the rest of the adapter for discipline).

2. Add `SunatGreFetchPort.fetch_many` as an optional protocol method in `ports.py`:
   ```python
   def fetch_many(self, urls: list[str], concurrency: int = 5) -> dict[str, Any | None]:
       """Optional batch fetch with bounded concurrency. Default impl loops fetch()."""
       return {url: self.fetch(url) for url in urls}
   ```
   (Optional default on the port prevents breaking existing test doubles that only implement
   `fetch()`.)

**Deliverables** (modify `backend/src/reconciliation/application/pipeline.py`):

Replace the sequential for-loop in `_stage_sunat_fetch` (lines 909-976) with a call to
`fetch_many` when the adapter supports it:
```python
# Bounded-concurrency batch path (D4)
import asyncio  # lazy in method body; already stdlib
urls = [block.gre_hashqr_url for block in blocks if block.gre_hashqr_url]
if hasattr(self._sunat, "fetch_many"):
    results = asyncio.run(self._sunat.fetch_many(urls, concurrency=5))
else:
    results = {url: self._sunat.fetch(url) for url in urls}
```
Then iterate `results` to apply SUNAT lines to blocks (same logic as the existing sequential
loop body — factor out the per-block application into a `_apply_sunat_result(block, official)`
helper to keep the stage readable).

**Tests** (new file `backend/tests/unit/adapters/test_sunat_fetch_many.py`):
- `fetch_many(urls, concurrency=3)` with 6 URLs and a mock `fetch` that returns a stub
  `OfficialGre` → all 6 results present in the returned dict; mock called exactly 6 times.
- Concurrency bound: using a counter + asyncio.Semaphore mock, assert no more than N tasks
  were in flight simultaneously (verify via a captured max-concurrent gauge).
- One URL whose mock `fetch` returns `None` → that key is `None` in the result; others populated.
- `fetch_many([])` (empty list) → returns `{}` with no calls to `fetch`.
- Backpressure: if 3+ consecutive `None` returns (simulated 429-origin), effective concurrency
  shrinks to max(1, N-1) — assert the shrink guard fires without crashing.
- Integration: `_stage_sunat_fetch` with a mock adapter that has `fetch_many` uses the batch
  path; a mock without `fetch_many` falls back to the sequential loop.

**Tests** (update `backend/tests/unit/domain/test_ports.py`):
- `SunatGreFetchPort.fetch_many` default implementation delegates to `fetch` for each URL.

**Test runner**: `cd backend && uv run pytest tests/unit/adapters/ tests/unit/domain/ -v --tb=short`

**Commit message**: `feat(sunat): add bounded-concurrency fetch_many via asyncio.Semaphore; wire in pipeline (CONT-S09/S11)`
**Completable in**: one to two sessions (medium — ~90 lines production across two files, ~70 lines tests).

---

### [x] R10.8 — `container.py`: wire `SunatConfig.cache_dir` to stable volume path

**Type**: `[CODE]` (TDD)
**Spec refs**: CONT-006 (cross-run cache), CONT-S10 (cache prevents re-fetch on second run).
**Depends on**: R10.5 (`SunatConfig.cache_dir` field exists), R10.7 (`fetch_many` API stable).
**Parallel with**: nothing after R10.7.

**Deliverables** (modify `backend/src/reconciliation/infrastructure/container.py`):

Replace line 460 (`sunat_cache_dir = ctx.run_dir / "sunat" if config.sunat.cache else None`):
```python
if config.sunat.cache:
    if config.sunat.cache_dir is not None:
        # D4: stable cross-run cache (e.g. /data/sunat-cache mounted volume)
        sunat_cache_dir = config.sunat.cache_dir
        sunat_cache_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Default: per-run cache (existing behavior — no cross-run reuse)
        sunat_cache_dir = ctx.run_dir / "sunat"
else:
    sunat_cache_dir = None
```
Log the resolution:
```python
logger.info(
    "build_pipeline: SUNAT cache_dir=%s (cross-run=%s)",
    sunat_cache_dir,
    config.sunat.cache_dir is not None,
)
```

**Tests** (update `backend/tests/unit/infrastructure/test_container.py`):
- `build_pipeline` with `sunat.cache=True, sunat.cache_dir=None` → `SunatDescargaqrAdapter`
  receives a per-run-dir path (`ctx.run_dir / "sunat"`).
- `build_pipeline` with `sunat.cache=True, sunat.cache_dir=Path("/data/sunat-cache")` →
  adapter receives `/data/sunat-cache`; path is created (mock `mkdir` or use tmp_path).
- `build_pipeline` with `sunat.cache=False` → adapter receives `cache_dir=None`.

**Test runner**: `cd backend && uv run pytest tests/unit/infrastructure/ -v --tb=short`

**Commit message**: `feat(infra): wire SunatConfig.cache_dir to stable cross-run volume path (CONT-S10)`
**Completable in**: one session (small — ~15 lines production, ~20 lines tests).

---

## Slice R10-E — Empirical Validation Gates

> Sequential. MUST happen in order. R10.9 is the fail-fast gate before R10.10.
> These tasks require: Ollama host daemon running with `qwen3.5:397b-cloud` pulled,
> real PDF at `./input/CTR-PLC01-FR001.pdf`, and `make build` completed.

### [x] R10.9 — Cloud-vision accuracy smoke + crop calibration

**Type**: `[EMPIRICAL]`
**Spec refs**: CONT-003 (cloud vision config-only), CONT-005 (ROI crop), CONT-S04, CONT-S07, CONT-S08.
**Depends on**: R10.4 (`make smoke` target), R10.5 (protocolo_crop config wired), R10.6 (meter hook), R10.2/R10.3 (image builds and compose running).
**Parallel with**: nothing — must pass before R10.10.
**Pre-condition**: `make build` passes; Ollama cloud daemon running on host.

**Deliverables** (new file `backend/tests/e2e/test_smoke_cloud_vision.py`):

Smoke test structure (marked `pytest.mark.e2e` or `pytest.mark.slow`):
```python
"""Cloud-vision accuracy smoke for r10-containerized-verification.

Pass criteria (D6):
  - Cloud qwen3.5:397b-cloud reads Registro 232 Protocolo page as date "2026-05-28"
    with confidence >= 0.85.
  - Token consumption logged (meter.calls >= 1, meter.total_tokens > 0).
  - Compare against local 9b ground truth (the R7-proven pages).
"""
```

Test 1 — Protocolo page accuracy (Registro 232):
- Build `OpenAICompatibleVisionAdapter` with cloud config (reads from env; skip if
  `RECONCILIATION__VISION__PROVIDER` is not `ollama` or host unreachable).
- Apply `protocolo_crop=(0.60,0.04,1.00,0.22)` to the Protocolo page for Registro 232
  (render the crop from the real PDF using the existing `_prepare_protocolo_vision_image`
  helper, or open the PDF with PyMuPDF directly at a known page index).
- Call `adapter.read_handwritten_date(cropped_bytes)`.
- Assert `result.date == date(2026, 5, 28)` and `result.confidence >= 0.85`.
- Assert `adapter.meter.calls == 1` and `adapter.meter.total_tokens > 0` (CONT-S08).
- Log `adapter.meter` values for the consumption record.

Test 2 — Guía stamp accuracy (one of the R7-proven pages: page 4 or page 5):
- Apply `stamp_crop=(0.55,0.05,1.00,0.45)` to a known guía page.
- Assert the cloud model reads the same date as the R7 ground truth (recorded in
  `docs/DECISIONS.md` §rev-3 validation).
- Assert confidence >= 0.85.

**Calibration loop (manual, not a test assertion)**:
If Test 1 fails (wrong date or low confidence), the crop box must be tuned. The recommended
bisection procedure:
1. Widen the crop: try `(0.55,0.02,1.00,0.28)`.
2. Check the rendered crop visually with `python -c "from PIL import Image; ...show the crop..."`.
3. Adjust `protocolo_crop` defaults in R10.5 and/or the compose env in R10.3.
4. Re-run the smoke until Test 1 passes at >= 0.85.
Only proceed to R10.10 after the smoke passes.

**Run via**: `make smoke` (inside the container) or
`docker compose run --rm backend python -m pytest tests/e2e/test_smoke_cloud_vision.py -v -s`.

**Commit message**: `test(e2e): cloud-vision accuracy smoke — Registro 232 date read + meter check (CONT-S04/S07/S08)`
**Completable in**: one to two sessions (empirical — outcome depends on crop calibration).

---

### [ ] R10.10 — Faithful in-container full run: R8 gate + R9 gate

**Type**: `[EMPIRICAL]`
**Spec refs**: CONT-007 (R8 MATCH gate + R9 divergence gate), CONT-S12, CONT-S13.
**Depends on**: R10.9 passing (cloud vision calibrated and accurate).
**Parallel with**: nothing — this is the final faithful verification run.
**Pre-condition**: R10.9 smoke passes; Ollama host daemon running; SUNAT reachable.

**Deliverables** (new file `backend/tests/e2e/test_container_verification.py`):

Test structure (marked `pytest.mark.e2e` or `pytest.mark.slow`):
```python
"""Faithful in-container verification run — r10 gate.

Invokes POST /runs via the real FastAPI endpoint (BackgroundTask path), polls
GET /runs/{id} to completion, then asserts R8 and R9 gates.

R8 gate (CONT-S12):  section #4252 → status=MATCH, summed_qty=4.124 TN, match_method=deterministic
R9 gate (CONT-S13):  Registro 232 → at least one guía with handwritten_fecha divergence flagged
"""
```

Test 1 — R8 MATCH gate (CONT-S12):
- `POST /runs` with `pdf_path="/data/input.pdf"` (the mounted real PDF).
- Poll `GET /runs/{run_id}` until `status == "completed"` (or timeout — suggest 15 min max).
- Assert at least one `ReconciliationRow` where the row covers section `#4252`:
  `row.status == "MATCH"`, `row.summed_qty == Decimal("4.124")`, `row.match_method == "deterministic"`.
- Assert paddle was never imported: `docker compose run backend python -c "import paddle"` → non-zero
  (or assert via `CONT-S02` smoke from R10.2's baked assertion — no action needed here).

Test 2 — R9 fecha-divergence gate (CONT-S13):
- Use the same completed run result from Test 1.
- Assert at least one guía for Registro 232 has a `handwritten_fecha` value that diverges from
  the `gre_fecha` (electronic date), causing the guía to be flagged as a misfiled candidate.
- The flag condition: `guia.handwritten_fecha != guia.gre_fecha` (or equivalent field on the
  row schema). This is the R9 divergence criterion from the spec.

**Run via**: `make verify`.

**Commit message**: `test(e2e): faithful in-container run — R8 MATCH gate + R9 fecha-divergence gate (CONT-S12/S13)`
**Completable in**: one session of wall-clock time (~2-4 hrs for first run including SUNAT fetch; subsequent runs use cache and take minutes).

---

## Slice R10-F — Documentation

> Can overlap with slice R10-E in parallel once R10.8 is complete.

### [ ] R10.11 — `docs/DECISIONS.md` + `docs/HANDOFF.md`: cloud egress as opt-in deviation

**Type**: `[INFRA]` (documentation)
**Spec refs**: CONT-008 (DECISIONS.md + HANDOFF.md must document cloud egress), CONT-S14/S15 (air-gap and opt-in invariants documented).
**Depends on**: R10.8 (all code complete; docs capture the final decisions).
**Parallel with**: R10.9/R10.10 (docs can be drafted while the empirical gates run).

**Deliverables** (update `docs/DECISIONS.md`):
- Add a `r10-containerized-verification` section recording:
  - Cloud vision deviation: `provider=ollama, base_url=host.docker.internal:11434/v1,
    model=qwen3.5:397b-cloud` — opt-in (env only); air-gap default preserved; cloud key
    stays host-side (Ollama bearer, never in the container).
  - SUNAT egress deviation: `sunat.enabled=true` in compose (opt-in; air-gap default is `false`).
  - Cross-run SUNAT cache: `cache_dir=/data/sunat-cache` (named Docker volume, mounts only
    when compose is used; per-run default unchanged outside container).
  - Protocolo crop tuned to `(0.60,0.04,1.00,0.22)` (starting default; empirically calibrated
    in R10.9; env-overridable).
  - Token metering hook: per-call and aggregate logging added to `OpenAICompatibleVisionAdapter`;
    no domain impact.

**Deliverables** (update `docs/HANDOFF.md`):
- Update §"next steps" to reflect that r10 is complete (after R10.10 passes).
- Document the `make` recipe for verification (`make build && make smoke && make verify`).
- Note the Ollama-cloud prerequisite: host daemon must be running with `qwen3.5:397b-cloud` pulled.
- Reference `docker-compose.yml` env vars for crop/token/SUNAT tuning knobs.

**Commit message**: `docs: record r10 cloud egress and SUNAT deviation in DECISIONS + HANDOFF (CONT-S14/S15)`
**Completable in**: one session (< 30 min).

---

## Task Dependency Summary

```
Slice R10-A (Reproducibility):
  R10.1 (uv.lock commit)

Slice R10-B (Container Image + Compose):
  R10.1 ──▶ R10.2 (Dockerfile) ──▶ R10.3 (compose + .dockerignore) ──▶ R10.4 (Makefile)

Slice R10-C (Config):
  R10.4 ──▶ R10.5 (config.py extensions)

Slice R10-D (Adapter Code — parallel pair):
  R10.5 ──▶ R10.6 (token meter)   [parallel with R10.7]
  R10.5 ──▶ R10.7 (fetch_many)    [parallel with R10.6]
  R10.7 ──▶ R10.8 (container.py cache_dir wiring)

Slice R10-E (Empirical Gates — sequential, fan-in):
  R10.4 + R10.5 + R10.6 + R10.8 ──▶ R10.9 (smoke — FAIL FAST)
  R10.9 ──▶ R10.10 (full faithful run)

Slice R10-F (Docs):
  R10.8 ──▶ R10.11 (docs)   [parallel with R10.9/R10.10]
```

**Total tasks**: 11 · **Sequential bottlenecks**: R10.1→2→3→4→5, R10.9→10
**Parallel opportunities**: R10.6 ∥ R10.7; R10.11 ∥ R10.9/R10.10

---

## Invariants each task MUST not break

| Invariant | Enforced by |
|-----------|-------------|
| Domain core PURE — no SDK/IO in `domain/` | R10.5 (config only), R10.6/R10.7 (adapter-internal), R10.8 (container wiring) |
| `ocr.enabled=false` never instantiates paddle | Verified by CONT-S02 baked into Dockerfile build; smoke in R10.9 |
| VisionLLMPort — no vendor binding in domain | Cloud config is adapter-constructor params, not a domain field; port unchanged |
| MATCH EXACT(0), flag<0.85 | Untouched — reconciliation.py not modified |
| Input PDF read-only | RO bind-mount in compose (`:ro`); no Dockerfile layer bakes the PDF |
| Air-gap default preserved | `sunat.enabled=False` default + `vision.provider=anthropic` default; cloud is compose-env-only |
| SUNAT graceful-None contract | `fetch_many` catches per-URL None; stage continues unchanged |
| Adapters lazy-import heavy deps | `asyncio.to_thread` + lazy `httpx.AsyncClient` in `fetch_many` |
| Per-run output isolation | Named `run-output` volume; only `sunat-cache` is shared across runs |
| Token meter pure-adapter (no domain coupling) | `_TokenMeter` lives entirely in `openai_compatible.py`; pipeline only reads `.meter` if present (duck-typed) |
