# Exploration: optional-vision-key-ui

> Mirror of Engram topic `sdd/optional-vision-key-ui/explore` (observation #3268, 2026-06-12).

## Current State

**Vision config lifecycle**
- `AppConfig` is constructed ONCE at FastAPI startup via `lifespan()` (`main.py:44-45`): `config_path = os.environ.get("RECONCILIATION_CONFIG", "config.yaml")` → `AppConfig.from_yaml(config_path)`. Config is stored on `app.state.config` and is IMMUTABLE for the lifetime of the process.
- `VisionConfig.enabled` (bool, default True) is the master switch. Set to `false` in `docker-compose.app.yml` via `RECONCILIATION__VISION__ENABLED: "false"`.
- `VisionConfig._inject_env_api_keys` (model_validator) pulls `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from env at construction time for `anthropic` and `openai` providers. The `ollama` provider has no required key (uses `"ollama"` placeholder).
- **api_key is explicitly `exclude=True`** in `VisionProviderConfig` — it is never serialized to disk by design. The docstring states "Secrets (api_key) are intentionally env-only; never written to config.yaml."
- **Fail-fast invariant**: `AppConfig._validate_date_source` raises `ValueError` if `vision.enabled=False` AND `sunat.enabled=False` — no date source. In `docker-compose.app.yml`, SUNAT is enabled so this passes.

**Vision adapter selection (startup decision)**
- `container.py::build_pipeline`: if `config.vision.enabled` is False → injects `NullVisionAdapter` (Null Object pattern); otherwise calls `factory.build_vision_adapter(config)`.
- `build_reprocess_service`: same gate — `NullVisionAdapter` when vision disabled.
- Vision availability is a **startup-time decision only**. The config object is fixed; there is no runtime swap mechanism.
- `_require_vision_on_service` guard in `routes.py` (line 399): checks `isinstance(reprocess_service._vision, NullVisionAdapter)` → raises HTTP 503 `vision_disabled`.

**No capabilities/config endpoint exists**
- Searched `routes.py` for any `GET /config`, `/capabilities`, `/settings`, `/health` endpoint → none found.
- `AppConfigDep` is used by route handlers internally but never serialized to a response. The frontend has no way to query vision availability today.

**Frontend reprocess surfaces — no availability gate today**
- `GuiaDrillDown.vue` — the `[Acciones]` menu (per-guía in the Reconciliación tab): 3 items: Reasignar / Reprocesar / Corregir manual. `Reprocesar` always renders; no gate on vision availability.
- `ErroredGuiasPanel.vue` — `Reprocesar con IA` button per errored guía. Conditional on `guia.retry_attempted === true` (must try REINTENTAR first), but NOT on vision availability.
- `PendientesPorProcesarTab.vue` — `Procesar todos con IA` bulk button per Registro group. Always renders (except for the `NULL_REGISTRO_KEY` sentinel). No availability gate.
- Both tabs show 503 as a text error AFTER the user clicks and the call fails. No pre-click gating exists.

**Frontend navigation / settings surface**
- Router has 3 routes: `/` (upload), `/runs/:id` (review), `/historial` (run history).
- `App.vue` header: wordmark, conditional nav links (Nueva subida / Revisión), and `RunHistoryMenu` hamburger.
- `RunHistoryMenu` dropdown: [Nuevo batch] / [Batch actual] / [Historial] — 3 items, no settings item.
- There is no settings page/route, no settings panel, no modal for app configuration.

**Key/secret persistence — docker-compose.app.yml**
- The compose file defines two named volumes: `run-output` (`/data/runs`) and `sunat-cache` (`/data/sunat-cache`).
- No `config.yaml` file is mounted into the container — config is entirely env-var-driven.
- No secrets volume. The only way to inject config today is via Docker env vars (which require editing `docker-compose.app.yml` or a `.env` file and restarting the container).
- The Windows launcher (not part of this codebase) controls container lifecycle via compose commands.

---

## Gap Analysis

**Gap 1 — Availability gating**
No backend endpoint exposes vision status. No frontend component reads or reacts to vision availability. All reprocess buttons are permanently visible/clickable, returning a 503 error on click when vision is off.

**Gap 2 — In-app key settings**
No settings UI exists. No mechanism to persist a user-supplied key inside the container. The "api_key never in config.yaml" policy creates a tension: the simplest persistence vector (writing to config.yaml) is explicitly prohibited.

**Gap 3 — Config reload**
`AppConfig` is built once at startup and stored immutably on `app.state.config`. There is no hot-reload path. Enabling vision requires rebuilding the config with new env vars or a new config source.

---

## Approach Options

### Problem A — Availability Gating (hide/disable reprocess buttons)

**Option A1 — New `GET /api/v1/capabilities` endpoint**
Returns `{ vision_enabled: bool, sunat_enabled: bool }` from `app.state.config`. Frontend calls it once on startup (or caches in Pinia). All three reprocess surfaces gate on `capabilitiesStore.visionEnabled`. Clean, explicit, low coupling.
- Pros: thin backend change (1 route), explicit contract, easy to extend, testable independently, SA-5 validatable.
- Cons: one more network call at startup; capabilities could go stale if config changed mid-session (not relevant for restart-to-apply flow).
- Effort: Low.

**Option A2 — Embed vision_enabled in `GET /runs/{id}/status` or `GET /runs/{id}/table` response**
Add a `vision_enabled: bool` field to `RunStatusResponse` or `ReconciliationTableResponse`, sourced from the config at the time the run was created.
- Pros: no new endpoint, frontend already queries these on every review page load.
- Cons: tighter coupling (run status / table owns a config field), harder to query before any run exists, per-run rather than global.
- Effort: Low-Medium.

**Option A3 — Reuse run-creation response**
`POST /runs` already returns `run_id`; add `vision_enabled: bool` there. Frontend stores it in Pinia on upload.
- Pros: zero new endpoints, available immediately after upload.
- Cons: not available on cold-load (navigating to a run from history), requires Pinia persistence across sessions.
- Effort: Low, but incomplete for cold-load case.

**Recommendation for gating**: Option A1 (`GET /capabilities`). Clean, global, queryable at any time, future-proof for adding more capabilities (ocr_enabled, sunat_enabled).

### Problem B — Runtime Key Persistence (save key → vision enabled)

**Option B1 — Restart-to-apply via persisted key file in a Docker volume**
New compose volume `secrets:/data/secrets`. Backend reads an optional `/data/secrets/vision_api_key` file at startup; if present, injects it as an env override before `AppConfig` construction. Frontend has a new `POST /api/v1/settings/vision-key` endpoint that writes the key to `/data/secrets/vision_api_key`. Engineer pastes key → clicks save → app signals "restart needed" → Windows launcher restarts the container → backend reads file → vision enabled.
- Pros: architecturally clean (key file is a secrets volume, not config.yaml), deterministic (startup picks up the file), no live hot-swap complexity, no auth needed (local-first).
- Cons: requires a new named volume in compose (minor), requires launcher awareness of "restart needed" signal (launcher is out-of-codebase scope; could be a manual restart), writes a plaintext key to a Docker volume (acceptable for local-first personal tool).
- Effort: Medium.
- Tension with "api_key never in config.yaml": the key file is a SEPARATE file in a secrets volume, NOT config.yaml → policy satisfied.

**Option B2 — Hot-swap vision adapter without restart**
Replace `app.state.config` with a new `AppConfig` constructed with the new key. Replace the `ReprocessService._vision` in all active run entries with a new real adapter.
- Pros: no restart required; seamless UX.
- Cons: HIGH complexity — active run entries hold references to the old adapter (race condition during swap); `AppConfig` is pydantic-settings constructed once by design; thread safety; `_validate_date_source` must be re-run; the pipeline (already-completed runs) is unaffected anyway. Effectively rebuilding the process config at runtime — fragile, against the "constructed once" invariant. Likely over-engineering for a local-first single-user tool.
- Effort: High.

**Option B3 — Env file mutation + restart signal**
Backend exposes `POST /settings/vision-key` which writes to a `.env` file mounted as a bind-mount (not a named volume). Backend signals the launcher via a sentinel file or an OS signal to restart.
- Pros: uses existing pydantic-settings `.env` file support.
- Cons: `.env` file is a code artifact (git-tracked by convention); bind-mounting it creates a collision with the development workflow; launcher out-of-codebase scope; sentinel file mechanism is fragile.
- Effort: Medium, but messier than B1.

**Recommendation for key persistence**: Option B1 (volume-based secrets file + restart-to-apply). Cleanest data-path, satisfies the "never in config.yaml" policy, safe for a local-first tool.

---

## Component-level gating points (frontend)

If Option A1 (`GET /capabilities`) is chosen, the following files need conditional rendering changes:
- `frontend/src/features/review/GuiaDrillDown.vue` — `Reprocesar` menu item (line ~169): gate on `capabilitiesStore.visionEnabled`.
- `frontend/src/features/review/ErroredGuiasPanel.vue` — `Reprocesar con IA` button (line ~78): add `capabilitiesStore.visionEnabled` to the condition.
- `frontend/src/features/review/PendientesPorProcesarTab.vue` — `Procesar todos con IA` bulk button (line ~48): gate on `capabilitiesStore.visionEnabled`.

New: `frontend/src/stores/capabilities.ts` — Pinia store for `{ visionEnabled, sunatEnabled }` fetched from `GET /capabilities`.
New: `frontend/src/api/client.ts` — `getCapabilities()` function.

---

## Settings Panel surface

**Where it lives**: the natural home is a new item in `RunHistoryMenu.vue` dropdown (hamburger already in the header) → opens a modal/dialog (not a full route). This avoids a new route and follows the existing disclosure-menu pattern. Alternative: a new `/configuracion` route. Modal is simpler (no route, no navigation).

**Key input**: `<input type="password">` with a "Guardar" button. Backend validates the key, writes to secrets file, returns `{ restart_required: true }`. Frontend shows a "Reiniciar la aplicación para activar la visión" banner.

---

## Risks

1. **Fail-fast invariant**: vision.enabled=False + sunat.enabled=False raises ValueError at startup. The secrets-file approach enables vision at startup only when the key file exists AND is non-empty — otherwise falls back to the current vision-off config. Must never leave the app in an unstarted state. The `_validate_date_source` validator runs at `AppConfig` construction, not at runtime.
2. **"api_key never to config.yaml" policy**: the secrets volume file is a SEPARATE file (`/data/secrets/vision_api_key`), not config.yaml. Policy is satisfied. The file contains only the raw key string, not YAML. The backend must read it before pydantic-settings construction and inject it as an env override.
3. **Hexagonal purity**: the secrets-file reader belongs in `main.py` (infrastructure/startup), not in domain or application. A thin `_read_secret_file(path) -> str | None` helper in `main.py` is sufficient.
4. **SA-5 (visible-UX feature)**: All three reprocess button gating changes + the settings panel are visible-UX changes that require Playwright validation.
5. **Windows launcher restart**: the "save key → restart" flow depends on the launcher being able to restart the container. The backend can only write the file and signal "restart needed" — it cannot restart itself. The engineer must manually restart (or the launcher can watch for a sentinel file). This is out-of-scope for the backend/frontend codebase.
6. **Provider selection**: the settings panel must let the engineer select the provider in addition to the key — OR scope the first slice to a single baked provider (the deployed use case), deferring anthropic/openai to a later slice. (RESOLVED at proposal time: Ollama cloud-direct baked defaults; key-only field.)
7. **Volume added to docker-compose.app.yml**: a new named volume `secrets` must be added to the compose file. The Windows launcher must not wipe this volume on update.

---

## Open Questions for Proposal Phase

1. Which provider does the engineer actually use? (RESOLVED: Ollama Cloud direct, `https://ollama.com/v1`, model `kimi-k2.5`, baked defaults.)
2. Is a manual container restart acceptable, or does the launcher need to trigger the restart automatically? (RESOLVED: restart-to-apply; launcher handles restart; out of this codebase's scope.)
3. Should the settings panel also expose sunat.enabled / other config fields? (RESOLVED: key-only field.)
4. How does the secrets-file injection win over the compose `RECONCILIATION__VISION__ENABLED: "false"` env var? (RESOLVED in proposal: startup env override in `main.py` before AppConfig construction.)
5. Invalid-key risk at save time? (RESOLVED: test call to provider at save; persist only on HTTP 200.)
6. Should existing runs get the reprocess buttons enabled after vision is turned on, or only new runs? (Carried to spec phase — capabilities are global, so after restart all runs see vision enabled.)
