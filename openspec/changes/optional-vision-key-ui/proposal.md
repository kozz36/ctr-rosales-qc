# Proposal: Optional Vision Key UI

## Intent

The delivered app (`docker-compose.app.yml`) runs vision OFF (deterministic OCR + SUNAT) for a non-technical Windows quality engineer. Two problems: (1) the three "Reprocesar con IA" surfaces are dead buttons — clicking yields an opaque 503; (2) the engineer has no code/terminal/.env access, so she cannot enable AI reprocess with her own Ollama Cloud API key. This change makes vision availability visible in the UI and lets her paste her key in-app.

## Scope

### In Scope
- `GET /api/v1/capabilities` → `{vision_enabled, sunat_enabled}` from `app.state.config`.
- Pinia capabilities store; the 3 reprocess surfaces (`GuiaDrillDown.vue`, `ErroredGuiasPanel.vue`, `PendientesPorProcesarTab.vue`) become **visible but disabled + tooltip** ("Configurá tu API key en Ajustes para habilitar IA") when vision is off.
- `POST /api/v1/settings/vision-key`: backend makes a **test call** to the provider; persists only on HTTP 200; responds "key válida — reiniciá para activar" / "key inválida" + `{restart_required: true}`.
- Key persisted to a secrets file in a new `secrets:/data/secrets` volume (`docker-compose.app.yml`), behind a port/adapter. Never in config.yaml; never logged.
- Restart-to-apply: `main.py` reads the key file BEFORE `AppConfig` construction and injects env overrides (key + `RECONCILIATION__VISION__ENABLED=true`).
- Settings **modal** (API-key field only) opened from the `RunHistoryMenu.vue` hamburger.
- Baked defaults: provider Ollama cloud-direct `https://ollama.com/v1` (`Authorization: Bearer`), model `kimi-k2.5`.

### Out of Scope
- Hot-swap / no-restart enabling (config is constructed-once by design).
- Multi-provider key UI (Anthropic/OpenAI); model/base_url editing.
- The Windows launcher (separate workstream) — this change only depends on it restarting the container after `restart_required`.

## Capabilities

### New Capabilities
- `app-capabilities`: capabilities discovery endpoint + frontend availability store.
- `vision-key-settings`: settings modal, validate-before-save, secrets-file persistence, restart-to-apply contract.

### Modified Capabilities
- `review`: reprocess actions gated disabled-with-tooltip when vision unavailable (pre-click; backend 503 remains backstop).

## Approach

Explore options A1 + B1 (locked). Composition-root env injection in `main.py`: keep `RECONCILIATION__VISION__ENABLED: "false"` in compose; when a non-empty key file exists, overwrite `os.environ` before `AppConfig.from_yaml`. Rationale: compose keeps an explicit safe default; "vision on" has a single source of truth (valid key file); pydantic-settings reads env at construction, so the late override wins deterministically; fail-fast `_validate_date_source` is never violated (no key file → unchanged vision-off + SUNAT-on config). Provider stays behind `VisionLLMPort` — Ollama defaults are config, not a vendor binding.

Assumption (per `rules.proposal`): key validation costs one minimal chat-completions call at save time; vision accuracy unchanged (`requires_review=True` stays mandatory on vision-recovered lines).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/src/reconciliation/infrastructure/api/routes.py` | Modified | `GET /capabilities`, `POST /settings/vision-key` |
| `backend/src/reconciliation/infrastructure/` (new adapter) | New | Secrets-file store + provider key-check behind ports |
| `backend/src/reconciliation/main.py` | Modified | Read key file pre-`AppConfig`; env override |
| `frontend/src/stores/capabilities.ts`, `api/client.ts` | New/Modified | Capabilities store + API calls |
| `frontend/src/features/review/{GuiaDrillDown,ErroredGuiasPanel,PendientesPorProcesarTab}.vue` | Modified | Disabled + tooltip gating |
| `frontend/src/.../RunHistoryMenu.vue` + new settings modal | New/Modified | Ajustes entry + key modal |
| `docker-compose.app.yml` | Modified | `secrets` volume |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Startup fail-fast (`vision=false + sunat=false`) | Low | Injection only flips vision ON; absence of key file leaves current valid config |
| Key leaks (logs/config.yaml) | Med | Separate secrets file; redact in logs/responses; api_key stays `exclude=True` |
| Compose env hardcodes vision off | High (without fix) | `main.py` env override wins over compose env (chosen above) |
| Key valid at save, revoked later | Low | Existing 503 guard + error surfaces remain backstop |
| UI gating regressions | Med | SA-5: Playwright runtime validation mandatory for all UI changes |

## Rollback Plan

Revert PR(s); remove `secrets` volume line from compose. Without the volume/key file, startup injection is a no-op → app returns to current vision-off behavior. No data migration.

## Dependencies

- Windows launcher restart capability (external; manual restart acceptable fallback).
- A valid Ollama Cloud key for end-to-end validation.

## Success Criteria

- [ ] Vision off: all 3 reprocess surfaces render disabled with the tooltip; no dead-click 503s.
- [ ] Saving an invalid key → "key inválida", nothing persisted, vision stays off.
- [ ] Saving a valid key (provider test 200) → persisted to secrets volume + restart banner; after container restart, `GET /capabilities` reports `vision_enabled: true` and reprocess works end-to-end.
- [ ] Key never appears in config.yaml, logs, or API responses.
- [ ] SA-5 Playwright evidence for gating + settings modal flows.

## Delivery Note

Estimated ~600–800 changed lines across backend + frontend + compose → likely **chained PRs**: (1) backend capabilities + settings endpoint + startup injection + compose volume; (2) frontend capabilities store + gating + settings modal (SA-5).
