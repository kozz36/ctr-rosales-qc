# App Capabilities Specification

**Capability**: `app-capabilities`
**Change**: optional-vision-key-ui
**Type**: New (no existing spec)
**Date**: 2026-06-12

---

## Purpose

Provides a runtime discovery endpoint so the frontend can gate UI features against the active
server configuration without requiring a pipeline run. Reports which optional subsystems are
enabled without exposing secrets or sensitive config values.

---

## Requirements

### CAP-001 — Capabilities discovery endpoint

The system MUST expose `GET /api/v1/capabilities` that returns the runtime availability of
optional subsystems sourced from the active `AppConfig`.

The response payload MUST conform to:

| Field | Type | Source |
|---|---|---|
| `vision_enabled` | bool | `app.state.config.vision.enabled` |
| `sunat_enabled` | bool | `app.state.config.sunat.enabled` |

The endpoint MUST be queryable without an active pipeline run.
The endpoint MUST NOT require authentication beyond whatever the app-wide middleware enforces.
The endpoint MUST NOT include API keys, credentials, file paths, model names, or any sensitive
config value in the response payload.

#### Scenario CAP-001-S01: vision off, SUNAT on (deployed default)

- GIVEN `vision.enabled=false` and `sunat.enabled=true` in the active AppConfig
- WHEN `GET /api/v1/capabilities` is called
- THEN the response is `200 OK` with body `{"vision_enabled": false, "sunat_enabled": true}`

#### Scenario CAP-001-S02: vision on, SUNAT on (after key injection)

- GIVEN `vision.enabled=true` and `sunat.enabled=true`
- WHEN `GET /api/v1/capabilities` is called
- THEN the response is `200 OK` with body `{"vision_enabled": true, "sunat_enabled": true}`

#### Scenario CAP-001-S03: no secrets in payload

- GIVEN any AppConfig state
- WHEN `GET /api/v1/capabilities` is called
- THEN the response body contains ONLY `vision_enabled` and `sunat_enabled` fields
- AND no API key, file path, model name, or internal config value is present

#### Scenario CAP-001-S04: endpoint available without a run

- GIVEN no pipeline run has been started (run list is empty)
- WHEN `GET /api/v1/capabilities` is called
- THEN the response is `200 OK`
- AND no run-related 404 or dependency error is raised

---

### CAP-002 — Frontend capabilities store

The frontend MUST maintain a Pinia store that fetches and caches the capabilities payload from
`GET /api/v1/capabilities` on application startup.

The store MUST expose `vision_enabled: boolean` and `sunat_enabled: boolean` as reactive state
readable by any component.

The store MUST NOT re-fetch on every component mount; a single fetch at app startup is
sufficient.

#### Scenario CAP-002-S01: store populated on startup

- GIVEN the app initializes and the backend returns `{"vision_enabled": false, "sunat_enabled": true}`
- WHEN any component reads `capabilitiesStore.vision_enabled`
- THEN the value is `false`

#### Scenario CAP-002-S02: store reactive across components

- GIVEN the capabilities store reports `vision_enabled=false`
- WHEN three components read `capabilitiesStore.vision_enabled` independently
- THEN all three receive `false` from the same cached fetch

---

## MUST-NOT Invariants

- The endpoint MUST NOT expose `api_key`, `base_url`, model identifiers, file paths, or any
  value classified as a secret.
- The capabilities state MUST reflect the active runtime config; it MUST NOT be hardcoded in
  the frontend.
- Domain layer (`domain/`) MUST NOT be modified by this capability.
