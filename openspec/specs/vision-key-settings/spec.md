# Spec — Vision Key Settings

**Capability**: vision-key-settings
**Type**: New capability
**Date**: 2026-06-12

---

## Purpose

Allows a non-technical operator to submit an Ollama Cloud API key via the UI. The backend
validates the key with a live provider test call before persisting; an invalid key is rejected
and nothing is written. On next container restart, the presence of a valid key file causes the
composition root to inject vision-enabled configuration.

---

## Requirements

### VKS-001 — Validate-before-persist key submission endpoint

The system MUST expose `POST /api/v1/settings/vision-key` that accepts `{"key": "<string>"}`.

Before persisting, the endpoint MUST make a test call to the configured provider endpoint
using the submitted key. The baked defaults are `base_url=https://ollama.com/v1` (Ollama
cloud-direct) with `Authorization: Bearer <key>` and model `kimi-k2.5`.

Validation outcome rules:

| Provider HTTP response | Endpoint action |
|---|---|
| `200 OK` | Persist key to secrets store; respond `{"restart_required": true}` |
| `401 Unauthorized` | Reject; respond `4xx` with message; nothing persisted |
| Any other error | Reject; respond `4xx` or `503` with diagnostic message; nothing persisted |

The submitted key MUST NEVER be:
- Written to `config.yaml` or any YAML/JSON config file
- Logged at any log level
- Included in any API response body
- Stored in process environment before validation succeeds

#### Scenario VKS-001-S01: valid key — persisted, restart_required returned

- GIVEN the submitted key yields HTTP 200 from the provider test call
- WHEN `POST /api/v1/settings/vision-key` is called with that key
- THEN the key is written to the secrets store
- AND the response is `200 OK` with body `{"restart_required": true}`

#### Scenario VKS-001-S02: invalid key (401) — rejected, nothing persisted

- GIVEN the submitted key yields HTTP 401 from the provider test call
- WHEN `POST /api/v1/settings/vision-key` is called
- THEN the response is `4xx` (e.g., `400 Bad Request`) with a clear error message
- AND NO write to the secrets store occurs
- AND the vision configuration remains unchanged

#### Scenario VKS-001-S03: provider unreachable — rejected, nothing persisted

- GIVEN the provider endpoint is unreachable (network timeout)
- WHEN `POST /api/v1/settings/vision-key` is called
- THEN the response is `4xx` or `503` with a diagnostic message
- AND NO write to the secrets store occurs

#### Scenario VKS-001-S04: key never appears in logs or response

- GIVEN any submission outcome (valid, invalid, or error)
- THEN the submitted key string is absent from all log lines at all log levels
- AND the response body does NOT contain the submitted key value

---

### VKS-002 — Secrets-store persistence (port/adapter)

The system MUST persist a validated key to a secrets file in a dedicated volume
(`/data/secrets/` by default) behind a port/adapter boundary.

The secrets file MUST be separate from `config.yaml`.
The secrets adapter MUST be injected at the infrastructure layer; the domain and application
layers MUST NOT reference the secrets file path or format directly.

#### Scenario VKS-002-S01: secrets file written only after successful validation

- GIVEN a successful validation (VKS-001-S01)
- WHEN the key is persisted
- THEN a secrets file exists in the configured secrets volume
- AND `config.yaml` is UNMODIFIED

#### Scenario VKS-002-S02: secrets file absent does not crash startup

- GIVEN no secrets file exists in the volume on application startup
- WHEN the composition root initializes
- THEN the application starts normally with vision disabled
- AND no exception is raised due to the missing secrets file

---

### VKS-003 — Composition-root restart-to-apply injection

On application startup, the composition root MUST check for a non-empty key file in the
secrets volume BEFORE constructing `AppConfig`.

If a non-empty key file exists:
- The environment MUST be overridden with the key value and `RECONCILIATION__VISION__ENABLED=true`
- `AppConfig` MUST be constructed AFTER this override so pydantic-settings reads the injected values
- The resulting `AppConfig` MUST have `vision.enabled=True`

If no key file exists (absent or empty):
- No environment override is applied
- `AppConfig` is constructed from the existing environment (compose default: vision off)
- The fail-fast invariant (`vision.enabled=false + sunat.enabled=false` → reject) MUST NOT be
  triggered by the absence of a key file

#### Scenario VKS-003-S01: key file present — vision enabled after restart

- GIVEN a non-empty secrets file in the volume
- WHEN the container restarts and the composition root initializes
- THEN `AppConfig.vision.enabled` is `True`
- AND `GET /api/v1/capabilities` returns `{"vision_enabled": true, ...}`

#### Scenario VKS-003-S02: key file absent — vision off, no fail-fast

- GIVEN no secrets file in the volume (default deployed state)
- WHEN the container starts
- THEN `AppConfig.vision.enabled` is `False` and `AppConfig.sunat.enabled` is `True`
- AND the application starts without error
- AND the fail-fast invariant is not violated

#### Scenario VKS-003-S03: fail-fast invariant preserved with key injection

- GIVEN a non-empty key file exists
- AND compose sets `RECONCILIATION__SUNAT__ENABLED=false`
- WHEN the composition root injects vision-enabled
- THEN `AppConfig` has `vision.enabled=True` and `sunat.enabled=False`
- AND the fail-fast invariant is NOT violated (vision provides the date source)

---

### VKS-004 — Settings modal (UI)

The frontend MUST expose a Settings modal accessible from the `RunHistoryMenu` hamburger menu
entry labelled "Ajustes" (or equivalent).

The modal MUST contain:
- A password-type input field for the API key
- A submit button labelled "Guardar y validar" (or equivalent)
- A visible status indicator: idle / saving / success ("key válida — reiniciá para activar") /
  error ("key inválida" or network diagnostic)

The modal MUST NOT display the previously saved key value (write-only UX).
The modal MUST NOT expose `base_url`, model, or provider configuration fields.

After a successful save, the modal MUST display a restart-required notice.
After a failed save, the modal MUST display the error message returned by the backend.

#### Scenario VKS-004-S01: valid key — success state and restart notice shown

- GIVEN the engineer submits a valid key in the Settings modal
- AND the backend returns `{"restart_required": true}`
- THEN the modal transitions to a success state
- AND a restart-required notice is visible
- AND the key field is cleared

#### Scenario VKS-004-S02: invalid key — error state shown; vision stays off

- GIVEN the engineer submits an invalid key
- AND the backend returns a `4xx` error
- THEN the modal transitions to an error state with the backend message visible
- AND the capabilities store still reports `vision_enabled=false`

#### Scenario VKS-004-S03: modal accessible from hamburger menu

- GIVEN the engineer opens the `RunHistoryMenu` hamburger
- WHEN they click "Ajustes"
- THEN the Settings modal opens

#### Scenario VKS-004-S04: previously saved key not displayed

- GIVEN a key was previously saved
- WHEN the engineer reopens the Settings modal
- THEN the key input field is empty (not pre-populated with the stored value)

---

## MUST-NOT Invariants

- The submitted key MUST NOT be logged at any level.
- The key MUST NOT be written to `config.yaml` or any config YAML/JSON file.
- The key MUST NOT appear in any API response body.
- Validation MUST precede persistence; persisting an unvalidated key is PROHIBITED.
- The fail-fast invariant (`vision.enabled=false + sunat.enabled=false`) MUST NOT be
  reachable through any code path introduced by this capability.
- Domain layer (`domain/`) MUST NOT be modified. No vision adapter code changes required for
  the validation test call — the test call uses the secrets-port adapter only.
- `VisionLLMPort` (the provider-agnostic vision boundary) MUST NOT be removed or replaced by
  a vendor-specific import in any layer.
