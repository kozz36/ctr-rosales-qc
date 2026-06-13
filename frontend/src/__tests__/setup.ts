/**
 * Global vitest setup.
 *
 * Installs a fresh, active Pinia before every test so that components which
 * read a Pinia store at setup() time (e.g. the SDD#4 vision-key gating via
 * `useCapabilitiesStore`) mount without each test having to wire Pinia.
 *
 * Tests that need to seed store state still create and activate their OWN Pinia
 * (`setActivePinia(createPinia())`) inside their `beforeEach`; that call simply
 * replaces the global instance for that test. This default only guarantees that
 * "there is an active Pinia" so store-reading components never throw
 * `getActivePinia() was called but there was no active Pinia`.
 */

import { beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

beforeEach(() => {
  setActivePinia(createPinia())
})
