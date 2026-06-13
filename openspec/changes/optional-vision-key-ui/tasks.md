# Tasks: Optional Vision Key UI (SDD#4)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR-1: ~380–460 (backend); PR-2: ~420–520 (frontend) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR-1 (backend) → PR-2 (frontend, stacked to main) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Capabilities endpoint + VisionKeyStorePort + file adapter + key probe + POST /settings/vision-key + lifespan injection + compose volume | PR-1 | Merges to main; no frontend dependency |
| 2 | Capabilities Pinia store + API client + disabled gating ×3 + VisionKeySettingsModal + RunHistoryMenu wiring + SA-5 validation | PR-2 | Stacked on main after PR-1 merges |

---

## PR-1: Backend

### Phase 1: Ports and Schemas (foundation)

- [x] **1.1 RED** — Write failing tests for `VisionKeyStorePort` contract: `read()` returns `None` when file absent; `read()` returns stripped string when present; `write()` creates file `chmod 0600` atomically; `write()` + `read()` roundtrip. Runner: `cd backend && uv run pytest tests/unit/application/test_vision_key_store.py`. Covers VKS-002-S01/S02.
- [x] **1.2 GREEN** — Create `backend/src/reconciliation/application/vision_key_store.py`: `VisionKeyStorePort(Protocol)` with `read() -> str | None` and `write(key: str) -> None`; `VisionKeyProbePort(Protocol)` with `probe(key: str) -> KeyProbeResult`; `KeyProbeResult` dataclass (`ok: bool`, `reason: Literal["valid","unauthorized","unreachable","error"]`, `message: str`). Pure typing — no IO. Make tests green.
- [x] **1.3 RED** — Write failing tests for `CapabilitiesResponse` schema: only two boolean fields; no `api_key`, path, or model field present. Also `VisionKeySaveRequest(key: str, min_length=1)`. Runner: `cd backend && uv run pytest tests/unit/infrastructure/api/test_capabilities_schemas.py`. Covers CAP-001-S03.
- [x] **1.4 GREEN** — Add to `backend/src/reconciliation/infrastructure/api/schemas.py`: `CapabilitiesResponse(BaseModel)` with `vision_enabled: bool`, `sunat_enabled: bool` (only these two fields); `VisionKeySaveRequest(key: str, min_length=1)`; `VisionKeySaveResponse(restart_required: bool)`. Make tests green.

### Phase 2: File Adapter

- [x] **2.1 RED** — Write failing tests for `VisionKeyFileStore`: missing dir → `read()` returns `None`, no exception; empty file → `read()` returns `None`; write → file present, permissions `0o600`, content stripped; atomic write (tmp + `os.replace`) — verify no partial-write exposure. Runner: `cd backend && uv run pytest tests/unit/infrastructure/test_vision_key_file_store.py`. Covers VKS-002-S01/S02.
- [x] **2.2 GREEN** — Create `backend/src/reconciliation/infrastructure/vision_key_file_store.py`: implements `VisionKeyStorePort`; path `/data/secrets/vision_api_key` (overridable via `RECONCILIATION_SECRETS_DIR` env); `mkdir(parents=True, exist_ok=True)` on write; atomic write via tmp + `os.replace`; `chmod 0600`; `read()` returns `None` on `FileNotFoundError` or empty/whitespace; value stripped. Key value NEVER logged — log only "vision key file found/absent". Make tests green.

### Phase 3: Key Probe Adapter

- [x] **3.1 RED** — Write failing tests for `VisionKeyProbeAdapter` using mocked `openai` client: valid → `KeyProbeResult(ok=True, reason="valid")`; `AuthenticationError` → `(ok=False, reason="unauthorized")`; `APIConnectionError`/timeout → `(ok=False, reason="unreachable")`; other exception → `(ok=False, reason="error")`; assert candidate key NOT in `caplog` output. Runner: `cd backend && uv run pytest tests/unit/adapters/vision/test_key_probe.py`. Covers VKS-001-S01–S04.
- [x] **3.2 GREEN** — Create `backend/src/reconciliation/adapters/vision/key_probe.py`: implements `VisionKeyProbePort`; lazy-imports `openai`; constructor bakes `base_url="https://ollama.com/v1"`, `model="kimi-k2.5"`, `max_tokens=1`, short timeout; uses candidate key per call (NOT stored globally); maps `openai.AuthenticationError → unauthorized`, `openai.APIConnectionError`/`openai.Timeout → unreachable`, all others → `error`; `message` is sanitized (no key echo). Make tests green.

### Phase 4: Capabilities and Settings Routes

- [x] **4.1 RED** — Write failing tests for `GET /api/v1/capabilities` via `TestClient`: vision off + SUNAT on → 200 `{"vision_enabled": false, "sunat_enabled": true}` (CAP-001-S01); vision on + SUNAT on → `{"vision_enabled": true, "sunat_enabled": true}` (CAP-001-S02); response body keys == `{"vision_enabled", "sunat_enabled"}` exactly (CAP-001-S03); no active run → 200 no error (CAP-001-S04). Runner: `cd backend && uv run pytest tests/unit/infrastructure/api/test_capabilities_route.py`.
- [x] **4.2 RED** — Write failing tests for `POST /api/v1/settings/vision-key` via `TestClient` with mocked probe+store: probe returns `valid` → 200 `{"restart_required": true}` + `store.write` called once (VKS-001-S01); probe returns `unauthorized` → 400 + `store.write` NOT called (VKS-001-S02); probe returns `unreachable` → 503 + `store.write` NOT called (VKS-001-S03); assert candidate key absent from all log lines (VKS-001-S04). Runner: `cd backend && uv run pytest tests/unit/infrastructure/api/test_settings_vision_key_route.py`.
- [x] **4.3 GREEN** — Modify `backend/src/reconciliation/infrastructure/api/routes.py`: add `GET /api/v1/capabilities` using `AppConfigDep`, returns `CapabilitiesResponse`; add `POST /api/v1/settings/vision-key` using `VisionKeySaveRequest`, calls `probe → write` via `app.state`-backed `Depends` (mirrors `_get_run_history`); flow: valid → `store.write(key)` → `VisionKeySaveResponse(restart_required=True)`; unauthorized → HTTP 400; unreachable/error → HTTP 503; key never logged/in response. Make both failing test suites green.

### Phase 5: Composition Root + Lifespan Injection

- [x] **5.1 RED** — Write failing tests for lifespan composition: tmp key file present → after lifespan setup, `os.environ["RECONCILIATION__VISION__ENABLED"] == "true"`, `os.environ["RECONCILIATION__VISION__PROVIDER"] == "ollama"`, key present in vision sub-config (not top-level log); no key file → env untouched, `AppConfig` built normally, no fail-fast (VKS-003-S01/S02/S03). Runner: `cd backend && uv run pytest tests/unit/infrastructure/api/test_main_lifespan.py`.
- [x] **5.2 GREEN** — Modify `backend/src/reconciliation/infrastructure/api/main.py` `lifespan()`: instantiate `VisionKeyFileStore` and `VisionKeyProbeAdapter` BEFORE line that calls `AppConfig.from_yaml`; call `key_store.read()`; if non-empty, set `os.environ`: `RECONCILIATION__VISION__ENABLED=true`, `RECONCILIATION__VISION__PROVIDER=ollama`, `RECONCILIATION__VISION__OLLAMA__API_KEY={key}`, `RECONCILIATION__VISION__OLLAMA__BASE_URL=https://ollama.com/v1`, `RECONCILIATION__VISION__OLLAMA__MODEL=kimi-k2.5`; expose `key_store` and `key_probe` on `app.state` for route `Depends`. Make tests green.

### Phase 6: Docker Compose Volume

- [x] **6.1** — Modify `docker-compose.app.yml`: add named volume `secrets:/data/secrets` for backend service; add volume declaration. Verify keyless startup (no secrets volume populated) leaves `vision.enabled=false` unchanged (VKS-002-S02). No change to dev `docker-compose.yml`.

### Phase 7: PR-1 Gate

- [x] **7.1** — Run full targeted backend suite: `cd backend && uv run pytest tests/unit/application/test_vision_key_store.py tests/unit/infrastructure/test_vision_key_file_store.py tests/unit/adapters/vision/test_key_probe.py tests/unit/infrastructure/api/test_capabilities_route.py tests/unit/infrastructure/api/test_settings_vision_key_route.py tests/unit/infrastructure/api/test_main_lifespan.py tests/unit/infrastructure/api/test_capabilities_schemas.py`. All green.
- [x] **7.2** — Work-unit commit sequence: `feat(capabilities): add GET /api/v1/capabilities endpoint` → `feat(vision-key): add VisionKeyStorePort + file adapter` → `feat(vision-key): add VisionKeyProbeAdapter (lazy openai, key probe)` → `feat(vision-key): add POST /settings/vision-key validate-before-persist route` → `feat(vision-key): inject key into lifespan composition root before AppConfig` → `chore(compose): add secrets volume to docker-compose.app.yml`. Conventional commits, no AI attribution.
- [ ] **7.3** — Judgment-day adversarial review before push. Focus: key-never-logged invariant, port boundary (no concrete adapter import in application/), lazy openai import, domain purity, fail-fast not triggerable via invalid key + SUNAT-off.

---

## PR-2: Frontend (stacked on main after PR-1 merges)

### Phase 8: API Client Types and Store

- [ ] **8.1 RED** — Write failing vitest for `useCapabilitiesStore`: initial state `visionEnabled=false`, `sunatEnabled=false`, `loaded=false`; after `fetch()` with mocked 200 `{vision_enabled: false, sunat_enabled: true}` → state updated; fetch failure → safe defaults kept, `loaded=false` or `loaded=true` with prior defaults (REV-R35-S01). Runner: `cd frontend && npm test`. Covers CAP-002-S01/S02, REV-R35-S01.
- [ ] **8.2 GREEN** — Create `frontend/src/stores/capabilities.ts`: `useCapabilitiesStore` (Pinia); state `visionEnabled=false`, `sunatEnabled=false`, `loaded=false`; action `fetch()` calls `getCapabilities()`, maps response, sets `loaded=true`; on network error keeps safe defaults. Single fetch — no per-component re-fetch logic here. Make tests green.
- [ ] **8.3 GREEN** — Extend `frontend/src/api/client.ts` with `getCapabilities(): Promise<CapabilitiesResponse>` and `saveVisionKey(key: string): Promise<VisionKeySaveResponse>`. Add corresponding types to `frontend/src/api/types.ts`: `CapabilitiesResponse`, `VisionKeySaveResponse`. No domain logic — pure HTTP wrappers.

### Phase 9: Disabled-not-Hidden Gating on 3 Surfaces

- [ ] **9.1 RED** — Write failing vitests for `GuiaDrillDown.vue` reprocess button gating: `visionEnabled=false` → button has `disabled` attribute, tooltip text present, element IN DOM (not `v-if` removed), no emit on click (REV-R34-S01/S03/S04); `visionEnabled=true` → button interactive, no tooltip (REV-R34-S02). Covers REV-R34, REV-R35-S02.
- [ ] **9.2 RED** — Write failing vitests for `ErroredGuiasPanel.vue` per-guía reprocess button: same disabled/tooltip/in-DOM/no-call contract; assert existing `v-if="retry_attempted"` visibility rule UNCHANGED (REV-R34-S03/S04).
- [ ] **9.3 RED** — Write failing vitests for `PendientesPorProcesarTab.vue` bulk "Procesar todos con IA" button: same disabled/tooltip/in-DOM/no-call contract (REV-R34-S01/S03/S04).
- [ ] **9.4 GREEN** — Modify `frontend/src/features/review/GuiaDrillDown.vue` ~L164: inject `useCapabilitiesStore`; bind `:disabled="!capabilitiesStore.visionEnabled || ...existing_conditions..."` and `:title="!capabilitiesStore.visionEnabled ? 'Vision IA no disponible — configurá una API key en Ajustes' : ''"` on reprocess button; NEVER use `v-if` for gating. Make test 9.1 green.
- [ ] **9.5 GREEN** — Modify `frontend/src/features/review/ErroredGuiasPanel.vue` ~L78: same gating pattern; preserve existing `v-if="retry_attempted"` visibility logic unchanged. Make test 9.2 green.
- [ ] **9.6 GREEN** — Modify `frontend/src/features/run/PendientesPorProcesarTab.vue` ~L49: same gating pattern on bulk button. Make test 9.3 green.

### Phase 10: VisionKeySettingsModal

- [ ] **10.1 RED** — Write failing vitests for `VisionKeySettingsModal.vue`: idle state renders password input + submit button; on submit emits POST; success state shows restart notice + key input cleared (VKS-004-S01/S04); error state shows backend message (VKS-004-S02); `value` of input never pre-populated from store (VKS-004-S04).
- [ ] **10.2 GREEN** — Create `frontend/src/features/settings/VisionKeySettingsModal.vue`: `<input type="password">` (write-only, no binding to stored key); \"Guardar y validar\" submit; state machine `idle → saving → success | error`; success shows \"Clave válida — reiniciá el servidor para activar Vision\"; error shows backend error message; on success clears input and shows restart notice; on cancel/close clears input. Make tests green.

### Phase 11: RunHistoryMenu Wiring and App Mount

- [ ] **11.1 RED** — Write failing vitest for `RunHistoryMenu.vue`: \"Ajustes\" menu item present; click opens `VisionKeySettingsModal` (modal visible). Covers VKS-004-S03.
- [ ] **11.2 GREEN** — Modify `frontend/src/features/run/RunHistoryMenu.vue`: add \"Ajustes\" item to hamburger menu; `v-if` or `ref`-controlled modal display; renders `VisionKeySettingsModal` when open. Make test green.
- [ ] **11.3 GREEN** — Modify `frontend/src/app/App.vue` `onMounted`: call `useCapabilitiesStore().fetch()` once. This ensures store is populated before any component reads it (CAP-002-S01/S02, REV-R35-S01 loading state).

### Phase 12: SA-5 Playwright Runtime Validation (mandatory gate)

- [ ] **12.1** — Start app locally (backend + frontend). Navigate to review UI with `vision.enabled=false` (default keyless). Assert: all three reprocess surfaces visible and disabled; hover shows tooltip; clicking disabled button triggers zero network requests (REV-R34-S01/S03/S04). Save evidence to `docs/playwright/sa5-optional-vision-key-ui-gating.json`.
- [ ] **12.2** — Open hamburger menu → click \"Ajustes\" → assert modal opens (VKS-004-S03). Submit empty key → assert error. Save evidence.
- [ ] **12.3** — Submit an invalid key via modal → assert error state with backend message; `GET /capabilities` still returns `vision_enabled: false` (VKS-004-S02, VKS-001-S02). Save evidence.
- [ ] **12.4 (real-data gate — real Ollama Cloud key required)** — Submit a valid Ollama Cloud key → assert success state + restart notice + key field cleared (VKS-004-S01). Restart backend → `GET /capabilities` returns `vision_enabled: true`. Navigate to reprocess → all three surfaces interactive. Trigger a real reprocess to confirm end-to-end. Save evidence. Flag as BLOCKED if no live key available — mark partial, do not mark PR-2 done.

### Phase 13: PR-2 Gate

- [ ] **13.1** — Run full frontend vitest suite: `cd frontend && npm test`. All existing 322 + new tests green.
- [ ] **13.2** — Work-unit commit sequence: `feat(capabilities): add useCapabilitiesStore + API client methods` → `feat(vision-key): disable-not-hide reprocess gating on 3 surfaces` → `feat(vision-key): add VisionKeySettingsModal` → `feat(vision-key): wire Ajustes to RunHistoryMenu + App.vue onMounted fetch`. Conventional commits, no AI attribution.
- [ ] **13.3** — Judgment-day adversarial review before push. Focus: `v-if` vs `disabled` (no DOM removal), store reactivity (REV-R35-S02), key input never pre-populated, modal error path, SA-5 evidence completeness.
