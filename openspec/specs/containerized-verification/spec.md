# Containerized Verification Specification
**Change**: r10-containerized-verification
**Domain**: containerized-verification (new domain; no prior spec to delta against)
**Phase**: spec
**Date**: 2026-06-02

---

## Purpose

Define the behavioural requirements for the reproducible-verification environment:
a deps-pinned container image + Compose configuration that runs the full backend test suite
and a real-PDF pipeline end-to-end faithfully, with cloud vision and SUNAT quantities, on
any machine without paddle, GPU, or per-host dependency drift.

This is a **new-domain full spec**. All existing domain requirements
(reconciliation, material-matching, etc.) remain in force unchanged.

---

## Requirements

### CONT-001 — Reproducible, paddle-free image

The backend container image MUST be built from a fully pinned dependency set (uv frozen
lockfile committed to the repository, base tag `python:3.12-slim` — never `:latest`).

The image MUST NOT contain paddle, paddleocr, or any GPU-runtime layer.

The image MUST run as a non-root user.

#### Scenario CONT-S01 — Image builds identically from lockfile

- GIVEN the committed uv.lock and Dockerfile are present
- WHEN `docker build` is executed on any machine with Docker installed
- THEN the image builds successfully without installing paddle or paddleocr
- AND the resulting image runs as a non-root user

#### Scenario CONT-S02 — Paddle import is absent at runtime

- GIVEN the container is started with `ocr.enabled=false`
- WHEN the pipeline initialises
- THEN no import of `paddle` or `paddleocr` is attempted anywhere in the process
- AND the pipeline reaches the reconciliation stage without raising an ImportError

---

### CONT-002 — Test suite runs green in-container without host drift

The container MUST run the full backend per-directory unit test suite.
All tests MUST pass (green) inside the container without any per-host dependency
modification, Conda environment, or manual installation step.

#### Scenario CONT-S03 — Backend tests pass inside the container

- GIVEN the built container image
- WHEN the test command is executed inside the container (e.g., `pytest backend/`)
- THEN all tests exit green (zero failures, zero errors)
- AND no paddle-related import is exercised during the test run

---

### CONT-003 — Vision is configurable to cloud via config only; domain core untouched

Switching the vision provider to a cloud model via Ollama MUST require only a configuration
change — no code modification.

The default cloud vision model is `kimi-k2.5` (83.1% on the #40 eval; outperforms
`qwen3.5:397b-cloud` at 76.9%). `qwen3.5:397b-cloud` remains a valid alternative.
When using the local Ollama proxy (default, `base_url=http://localhost:11434/v1`), the model
is specified with the `:cloud` suffix (e.g. `kimi-k2.5:cloud`). For direct Ollama Cloud
(`base_url=https://ollama.com/v1`), the bare name is used (e.g. `kimi-k2.5`) alongside
`OLLAMA_API_KEY`.

The domain core (`backend/src/reconciliation/domain/`) MUST NOT be modified by this
change. The `VisionLLMPort` interface MUST remain the sole point of coupling between
the domain and any vision implementation.

#### Scenario CONT-S04 — Cloud vision activated by config, not code

- GIVEN `vision.provider=openai`, `vision.base_url=<ollama_cloud_url>`,
  `vision.model=kimi-k2.5:cloud` (default) or `vision.model=qwen3.5:397b-cloud` (alternative) set in container config
- WHEN the pipeline runs a vision stage
- THEN the `OpenAICompatibleVisionAdapter` is selected automatically
- AND no domain-core file is modified
- AND the vision stage completes without raising a provider-binding error

#### Scenario CONT-S05 — Domain core files unchanged

- GIVEN the r10 change is fully applied
- WHEN `git diff main -- backend/src/reconciliation/domain/` is inspected
- THEN zero files in the domain layer are modified, added, or removed

---

### CONT-004 — SUNAT quantities; paddle MUST NOT be instantiated

When `ocr.enabled=false` is set in the container environment, the pipeline MUST source
all guía quantities from SUNAT (deterministic) and MUST NOT instantiate the paddle
extractor at any point in the run.

#### Scenario CONT-S06 — NullOcrExtractor injected, paddle never imported

- GIVEN `ocr.enabled=false` in container config
- WHEN the pipeline runs end-to-end
- THEN `NullOcrExtractor` (or equivalent null adapter) is the active extractor
- AND no `PaddleOcrAdapter` or `paddleocr` import is executed
- AND quantities in the reconciliation output originate from SUNAT responses

---

### CONT-005 — Vision calls use ROI crops; consumption is observable

Each vision call MUST send only the relevant region-of-interest (guía date-stamp crop),
not the full page image.

The container run MUST log or surface token consumption per vision call and the aggregate
total for the run so it is observable without post-processing.

#### Scenario CONT-S07 — ROI crop sent, not full page

- GIVEN a guía page requiring a date read
- WHEN the vision adapter constructs the request
- THEN the image payload is the configured crop region (`stamp_crop`)
- AND `max_tokens` is set to the configured tight value
- AND the full-page image is NOT transmitted to the cloud endpoint

#### Scenario CONT-S08 — Token consumption is logged per call and in aggregate

- GIVEN the pipeline completes a run with N vision calls
- WHEN the run finishes
- THEN each vision call logs its token usage (prompt + completion tokens)
- AND the run log includes an aggregate total token count for the vision stage

---

### CONT-006 — SUNAT fetches complete in minutes; bounded concurrency

The SUNAT fetch stage for a real-data run (~35 guías) MUST complete in a time bounded
to minutes, not the ~30 min sequential baseline observed on this host.

The fetch implementation MUST use bounded parallelism (capped concurrency window) and/or
a per-run-directory cache so that repeated runs do not re-fetch already-retrieved PDFs.

The fetch MUST respect a 429 / rate-limit backoff and MUST NOT raise an unhandled
exception on a transient network failure.

#### Scenario CONT-S09 — Parallel fetch completes faster than sequential

- GIVEN ~35 SUNAT guías to fetch with a real network connection
- WHEN the SUNAT fetch stage runs with bounded concurrency enabled
- THEN all fetches complete (or fail with captured errors) within a bounded wall-clock time
- AND the run does not time out from sequential blocking

#### Scenario CONT-S10 — Cache prevents redundant re-fetch on second run

- GIVEN a prior run has cached SUNAT PDFs in the run-dir
- WHEN the pipeline runs again against the same run-dir
- THEN already-cached PDFs are read from disk without issuing a new SUNAT HTTP request
- AND the run completes faster on the second execution

#### Scenario CONT-S11 — 429 backoff does not crash the run

- GIVEN SUNAT returns HTTP 429 for one or more guía fetches
- WHEN the fetch adapter receives the 429 response
- THEN the adapter backs off and retries after the appropriate delay
- AND the run continues and completes; no unhandled exception is raised

---

### CONT-007 — Faithful in-container verification: R8 gate + R9 gate

A complete in-container pipeline run against the real input PDF MUST reproduce:

- **R8 gate**: section #4252 reconciles to `status=MATCH`, `summed_qty=4.124 TN` for
  `BARRA A615 G60 1/2" x 9M`.
- **R9 gate**: Registro 232 surfaces at least one guía with `handwritten_fecha` diverging
  from `fecha_declarada`, triggering the misfiled-guía flag.

Both gates MUST be confirmed with cloud vision supplying the handwritten dates and SUNAT
supplying the quantities.

#### Scenario CONT-S12 — R8 MATCH gate passes in-container

- GIVEN the real input PDF mounted read-only in the container
- AND `ocr.enabled=false`, `sunat.enabled=true`, `vision.provider=openai` (cloud)
- WHEN the pipeline runs end-to-end
- THEN section #4252 produces `status=MATCH` with `summed_qty=4.124 TN`
- AND `match_method=deterministic`, `requires_review=False`

#### Scenario CONT-S13 — R9 fecha-divergence gate passes in-container

- GIVEN the same container run (cloud vision reads Protocolo "Fecha:" + guía stamp)
- WHEN the pipeline completes for Registro 232
- THEN at least one guía for Registro 232 has `handwritten_fecha != fecha_declarada`
- AND that guía surfaces the misfiled-guía flag
- AND the flag is present in the reconciliation output

---

### CONT-008 — Local-first / air-gap default preserved

The container configuration MUST NOT make cloud vision or SUNAT the unconditional default.

The following air-gap configuration MUST remain valid and documented:
- `vision.provider=ollama` (local), `sunat.enabled=false`, `ocr.enabled=true`
  (where local paddle works).

The cloud-vision + SUNAT profile MUST be an explicit, named opt-in (e.g., a separate
Compose override or environment file), not the only provided configuration.

DECISIONS.md and HANDOFF.md MUST document cloud vision + SUNAT egress as a recorded
deviation from the air-gap default, not as the new default.

#### Scenario CONT-S14 — Air-gap config remains valid

- GIVEN `vision.provider=ollama`, `sunat.enabled=false`, `ocr.enabled=true`
  set in the container environment
- WHEN the pipeline is started
- THEN no outbound HTTP request is made to the cloud vision endpoint or SUNAT
- AND the pipeline initialises without error (assuming local Ollama is reachable)

#### Scenario CONT-S15 — Cloud profile is opt-in, not the unconditional default

- GIVEN only the base `docker-compose.yml` is used (no cloud override applied)
- WHEN the pipeline runs
- THEN `sunat.enabled` defaults to `false` (or the equivalent configured air-gap default)
- AND no cloud vision endpoint is contacted unless explicitly overridden

---

### CONT-009 — Acceptance gate is executable, strict, and has a fast variant

The CONT-007 R8/R9 gate MUST be backed by a real, collectable in-container test file
(`backend/tests/e2e/test_container_verification.py`) invoked as `make verify`. The gate
is API-faithful: it submits the real PDF via `POST /api/v1/runs`, polls `GET
/api/v1/runs/{run_id}` until `status="review"`, then asserts on `GET
/api/v1/runs/{run_id}/table` — no in-process pipeline call, no mock.

Under `make verify`, the gate MUST run in strict mode (`CTR_VERIFY_STRICT=1`): a missing
precondition (backend unreachable after the configured wait window, PDF absent, pipeline
ending in `status="error"`) MUST cause the gate to FAIL, never silently skip. A vacuous
green (skip-all due to absent preconditions) is the exact mock-theatre failure class this
project has been burned by and is explicitly prohibited.

The gate MUST confirm it is communicating with the ctr backend by requiring `GET
/api/v1/runs` to return HTTP 200 with a JSON list before uploading the PDF. A foreign
service squatting on the host port that does not satisfy this contract MUST be rejected with
a clear gate failure.

The backend host port MUST be configurable via the `CTR_BACKEND_PORT` environment variable
(default `8010`) so the host-networking container coexists with sibling services that bind
port 8000. The container-internal binding (uvicorn, nginx proxy) stays on port 8000;
`CTR_BACKEND_PORT` controls only the host-side exposure and the `CTR_BACKEND_URL` injected
into the test process.

A fast variant `make verify-fast` MUST run the identical R8 + R9 assertions against a
section-safe page subset of the real PDF (Protocolo boundaries auto-detected so no registro
and its guías are split, keeping summed quantities intact), completing in minutes instead of
the ~90-minute full-document run. The subset is built on the host via
`backend/scripts/make_verify_subset.py` and mounted into the container via a Compose
override.

#### Scenario CONT-S16 — Strict mode fails on a missing precondition rather than skipping

- GIVEN `CTR_VERIFY_STRICT=1` (set by `make verify`)
- AND the backend is unreachable at the configured host port after the wait window
- WHEN the acceptance gate runs
- THEN `pytest` exits non-zero (gate FAILS with a descriptive message)
- AND the run is NOT marked green / skipped silently

#### Scenario CONT-S17 — verify-fast passes the same R8 + R9 assertions on a 3-section subset

- GIVEN the subset PDF built by `make_verify_subset.py` contains the first 3 complete
  Protocolo sections (registro 232 and its guías intact, ending on a Protocolo boundary)
- AND the container runs with `CTR_VERIFY_STRICT=1` against the subset PDF
- WHEN `make verify-fast` completes
- THEN all R8 MATCH assertions for registro 232 pass (status=MATCH, summed_qty=4.124 TN)
- AND all R9 fecha-divergence assertions pass
- AND the run completes in minutes (not ~90 min)

#### Scenario CONT-S18 — Backend port is configurable and the gate rejects a foreign host-port service

- GIVEN `CTR_BACKEND_PORT=8020` overrides the default
- AND a foreign HTTP service responds on port 8020 but does NOT return a JSON list from
  `GET /api/v1/runs`
- WHEN the acceptance gate attempts to verify the backend
- THEN the gate rejects the foreign service and FAILS with a "backend not reachable" message
- AND `make verify` with `CTR_BACKEND_PORT=8020` routes all traffic (upload + poll) to
  port 8020, not the default 8010

---

### CONT-010 — RapidOCR ONNX models bundled at build time (air-gap guarantee)

All three RapidOCR PP-OCRv5 ONNX models MUST be downloaded and cached into the image at
Docker build time:

- `ch_PP-OCRv5_det_server.onnx` (~84 MB) — Det
- `ch_PP-OCRv5_rec_server.onnx` (~81 MB) — Rec
- `ch_ppocr_mobile_v2.0_cls_mobile.onnx` (~0.6 MB) — Cls (PP-OCRv4 mobile; default
  config.yaml behaviour keeps Cls at v4/mobile even when Det/Rec are v5/server)

Bundling MUST be achieved via a warm-up inference on a synthetic text image (not just engine
construction): RapidOCR lazy-loads Cls and Rec only when Det detects text boxes. A
random-noise image produces zero Det boxes and leaves Cls and Rec unloaded, breaking the
air-gap guarantee. A synthetic image with legible strokes (rendered via `cv2.putText`) MUST
be used so Det finds boxes and all three models lazy-load.

After the warm-up, a disk-existence guard MUST verify that each of the three model files is
present and non-empty. The Docker build MUST FAIL LOUDLY (non-zero exit) if any model file
is absent.

A second disk-existence guard MUST run in the runtime stage (`ocr_assert.py`) to confirm
that `COPY --from=builder` carried all three models into the final image.

At runtime, OCR inference MUST require zero network for model loading, proven by the ability
to run `docker run --network none` without any model download.

#### Scenario CONT-S19 — Build fails if any of the 3 ONNX model files is absent after warm-up

- GIVEN the builder warm-up inference completes
- AND one or more of the three required ONNX model files is missing or empty in
  `site-packages/rapidocr/models/`
- WHEN the disk-existence guard (`ocr_warmup.py`) runs
- THEN the script exits non-zero and the Docker build FAILS with a descriptive error
- AND the runtime image is NOT produced

#### Scenario CONT-S20 — OCR inference under --network none succeeds with no download

- GIVEN the fully-built container image (all three ONNX models bundled)
- WHEN `docker run --network none` launches a RapidOCR PP-OCRv5-server inference
- THEN the inference completes successfully without any outbound network call
- AND no model download is attempted at runtime

---

## Out of scope for this change

- GPU passthrough / nvidia-container-toolkit.
- Paddle in the container or fixing the local oneDNN/PIR paddle build.
- Kubernetes / K3s orchestration.
- Production deployment, CI/CD, reverse proxy, TLS, monitoring, or backup.
- Domain-core changes (grouping, matching, unit rules, reconciliation logic).
- New vision providers or changes to the `VisionLLMPort` interface contract.
- Unit conversion between KG / TN / RD / Rollo (domain invariant: prohibited).
