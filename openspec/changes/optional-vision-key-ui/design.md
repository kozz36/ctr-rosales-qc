# Design: Optional Vision Key UI

(Mirrored at Engram `sdd/optional-vision-key-ui/design`)

## Technical Approach

Hexagonal, additive. Backend: a read-only capabilities endpoint (CAP-001), a `VisionKeyStorePort` Protocol + file adapter for the secret (VKS-002), a provider key probe for validate-before-persist (VKS-001), and Composition-Root env injection in `lifespan()` before `AppConfig.from_yaml` (VKS-003). Frontend: a Pinia capabilities store driving disabled-not-hidden gating on the 3 reprocess surfaces (REV-R34/R35) plus a key-only settings modal off the `RunHistoryMenu` hamburger (VKS-004). Domain layer untouched.

## Architecture Decisions

### D1 — Dedicated `GET /api/v1/capabilities`
**Choice**: New route in `routes.py` using `AppConfigDep`; `CapabilitiesResponse(BaseModel)` in `schemas.py` with ONLY `vision_enabled: bool`, `sunat_enabled: bool` (CAP-001-S03: no secrets/paths/models).
**Rejected**: embedding the flag in run status/table responses (couples config to run resources; unavailable pre-run).
**Rationale**: global, run-independent, extensible; mirrors existing thin-route + pydantic-response pattern.

### D2 — Secret behind `VisionKeyStorePort` (file adapter, secrets volume)
**Choice**: `application/vision_key_store.py` — `VisionKeyStorePort(Protocol)` with `read() -> str | None` and `write(key: str) -> None` (typing-only, mirrors `RunHistoryPort`). Adapter `infrastructure/vision_key_file_store.py`: `/data/secrets/vision_api_key` (dir overridable via env `RECONCILIATION_SECRETS_DIR`, same precedent as `RECONCILIATION_CONFIG`); atomic write (tmp + `os.replace`), `chmod 0600`, `mkdir(parents=True)`, `read()` returns `None` on missing/empty, value stripped. NEVER under `domain/`.
**Rejected**: hot-swap config (violates constructed-once `AppConfig`; adapter-reference races), `.env` mutation (git-tracked artifact; dev-workflow collision), writing config.yaml (explicitly prohibited — `api_key` is `exclude=True` by policy).
**Rationale**: the key lives in a raw secrets file in a dedicated volume — config.yaml untouched, satisfying "api_key never in config.yaml". Secret value never logged; log only "vision key file present/absent".

### D3 — Thin key probe, NOT `OpenAICompatibleVisionAdapter` reuse
**Choice**: `VisionKeyProbePort(Protocol)` (same application module) with `probe(key: str) -> KeyProbeResult` (`ok: bool`, `reason: "valid"|"unauthorized"|"unreachable"|"error"`, sanitized `message`). Adapter `adapters/vision/key_probe.py`: lazy-imports `openai`, minimal `chat.completions.create` (model `kimi-k2.5`, base_url `https://ollama.com/v1`, `max_tokens=1`, short timeout) with the CANDIDATE key; maps `AuthenticationError → unauthorized`, connection/timeout → `unreachable`. Defaults are baked constructor params (config, never a domain binding — `VisionLLMPort` untouched).
**Rejected**: reusing `OpenAICompatibleVisionAdapter` — its error-isolation contract never raises, making 401 indistinguishable from a benign empty read; VKS-001 requires that distinction.

### D4 — Composition-Root env injection (before `AppConfig.from_yaml`)
**Choice**: in `infrastructure/api/main.py::lifespan`, BEFORE line 44-45: `key = key_store.read()`; if non-empty set `os.environ`: `RECONCILIATION__VISION__ENABLED=true`, `RECONCILIATION__VISION__PROVIDER=ollama` (**gap fixed**: brief omitted PROVIDER; coded default is `anthropic` — without it the factory builds the wrong adapter), `RECONCILIATION__VISION__OLLAMA__API_KEY={key}`, `...__OLLAMA__BASE_URL=https://ollama.com/v1`, `...__OLLAMA__MODEL=kimi-k2.5`.
**Rationale**: pydantic-settings reads `os.environ` at construction (priority env > yaml > defaults), so a late same-process overwrite of compose's `RECONCILIATION__VISION__ENABLED: "false"` deterministically wins — compose keeps an explicit safe default while "vision on" has a single source of truth (valid key file). Fail-fast `_validate_date_source` stays safe: injection only flips vision ON; no key file → zero mutation → current valid vision-off+SUNAT-on config (VKS-003-S02). Patterns: **Composition Root** (one wiring point), **Null Object** (`NullVisionAdapter` path unchanged when keyless), Strategy factory untouched.

### D5 — `POST /api/v1/settings/vision-key`: probe → persist → restart contract
Request `VisionKeySaveRequest(key: str, min_length=1)`; flow: `probe(key)` → `valid`: `store.write(key)` + `200 {"restart_required": true}`; `unauthorized`: 400, nothing persisted; `unreachable|error`: 503, nothing persisted. Key never in env/log/response at save time. Store+probe built in `lifespan`, exposed via `app.state` + `Depends` (mirrors `_get_run_history`). Restart is the launcher's job (out of repo); manual fallback documented: `docker compose -f docker-compose.app.yml restart backend`.

### D6 — Frontend: store-driven disabled-not-hidden gating; modal, no route
`stores/capabilities.ts`: `useCapabilitiesStore` — `visionEnabled=false`, `sunatEnabled=false`, `loaded=false` defaults; `fetch()` once at app mount (`App.vue` onMounted); fetch failure keeps safe defaults (REV-R35-S01: disabled while loading). Gating adds `:disabled` + tooltip (NOT `v-if` — REV-R34-S03 keeps controls in DOM): `GuiaDrillDown.vue` ~L164, `ErroredGuiasPanel.vue` ~L78 (keep existing `v-if="retry_attempted"` visibility rule), `PendientesPorProcesarTab.vue` ~L49. New `features/settings/VisionKeySettingsModal.vue`: masked password input, "Guardar y validar", idle/saving/success("key válida — reiniciá para activar")/error states, never pre-populates saved key; opened from a new "Ajustes" item in `RunHistoryMenu.vue`. `api/client.ts`: `getCapabilities()`, `saveVisionKey(key)`.

## Data Flow

    Save:    Modal ──POST /settings/vision-key──▶ route ──▶ KeyProbe(candidate) ──200──▶ KeyStore.write ──▶ {restart_required}
    Restart: key file ──read in lifespan──▶ os.environ overrides ──▶ AppConfig.from_yaml ──▶ vision.enabled=true ──▶ factory→Ollama adapter
    Gating:  App mount ──GET /capabilities──▶ Pinia store ──▶ :disabled on 3 reprocess surfaces

## File Changes

| File | Action |
|---|---|
| `backend/src/reconciliation/application/vision_key_store.py` | Create — `VisionKeyStorePort`, `VisionKeyProbePort`, `KeyProbeResult` |
| `backend/src/reconciliation/infrastructure/vision_key_file_store.py` | Create — file adapter |
| `backend/src/reconciliation/adapters/vision/key_probe.py` | Create — OpenAI-compat probe (lazy SDK) |
| `backend/src/reconciliation/infrastructure/api/{routes,schemas}.py` | Modify — 2 routes, 3 schemas |
| `backend/src/reconciliation/infrastructure/api/main.py` | Modify — lifespan key read + env injection |
| `docker-compose.app.yml` | Modify — `secrets:/data/secrets` named volume (dev compose untouched; missing dir handled gracefully) |
| `frontend/src/stores/capabilities.ts`, `features/settings/VisionKeySettingsModal.vue` | Create |
| `frontend/src/api/{client,types}.ts`, `app/App.vue`, `features/run/RunHistoryMenu.vue`, 3 review components | Modify |

## Testing Strategy (Strict TDD ACTIVE)

| Layer | What | How |
|---|---|---|
| Backend unit (`cd backend && uv run pytest <targeted>` — never monolithic, paddle hang) | file store (write/read/missing/empty/0600/atomic); capabilities (S01–S04, TestClient + stub config); settings route (probe mocked valid/401/timeout → persist-or-not; `caplog` asserts key absent); composition root (tmp key file + monkeypatch → `vision.enabled=True`, provider=ollama, key on sub-config; absent → unchanged, no fail-fast); probe (mocked openai errors) | failing test FIRST per slice |
| Frontend vitest (`cd frontend && npm test`) | store defaults/fetch/failure; 3 gating specs (disabled+tooltip+in-DOM, reactive flip); modal states + never-displays-key | failing test FIRST |
| Runtime (SA-5, mandatory gate) | Playwright vs running app: vision-off → 3 surfaces disabled+tooltip, no API call; Ajustes → modal; invalid-key error path. Valid-key e2e (restart → capabilities true → reprocess works) needs a real Ollama Cloud key — real-data check, not mock theatre | evidence in `docs/playwright/` |

## Migration / Rollout

No data migration. Rollback: revert PRs + drop `secrets` volume line — keyless startup is a no-op (current behavior).

## Slices / Review Workload

- **PR-1 (backend)**: ports + adapters + 2 endpoints + lifespan injection + compose volume (~350–450 changed lines incl. tests).
- **PR-2 (frontend)**: store + client + gating ×3 + modal + SA-5 (~400–500 lines).
- 400-line budget risk: **High** per PR once tests counted → chained PRs recommended (matches proposal delivery note). Final guard lines belong to sdd-tasks.

## Open Questions

- [ ] Launcher restart signal (out of repo): file sentinel vs manual restart — design assumes manual fallback is acceptable (proposal: yes).
