/**
 * Capabilities store — run-independent feature flags (SDD#4, CAP-002).
 *
 * Fetches GET /capabilities ONCE at app mount and caches the result. Drives the
 * disabled-not-hidden gating of the three AI reprocess surfaces (REV-R34/R35).
 *
 * Fail-safe contract (REV-R35-S01): `visionEnabled` defaults to FALSE and STAYS
 * false while the fetch is in-flight AND on fetch error. A vision-gated control
 * must never become enabled by accident — the only path to `true` is a
 * successful capabilities response reporting `vision_enabled: true`.
 *
 * Pattern: Pinia for client-cached feature flags. This is config-at-startup,
 * not per-run server state, so it lives in Pinia (not TanStack Query) and is
 * fetched exactly once — `loaded` gates the single fetch.
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getCapabilities } from '@/api/client'

export const useCapabilitiesStore = defineStore('capabilities', () => {
  // ---------------------------------------------------------------------------
  // State — fail-safe disabled defaults (REV-R35-S01)
  // ---------------------------------------------------------------------------

  /** True only after a successful response reporting vision_enabled=true. */
  const visionEnabled = ref(false)

  /** True only after a successful response reporting sunat_enabled=true. */
  const sunatEnabled = ref(false)

  /**
   * True once a fetch has SUCCEEDED. Gates the single-fetch contract
   * (CAP-002-S02). A failed fetch keeps this false so App.vue / a later caller
   * may retry without being blocked by the once-only guard.
   */
  const loaded = ref(false)

  /** Guards against overlapping in-flight fetches. */
  const inFlight = ref(false)

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  /**
   * Fetch capabilities once. No-op when already loaded or a fetch is in-flight.
   * On error the safe disabled defaults are retained (never throws) so the
   * caller (App.vue onMounted) does not need a try/catch.
   */
  async function fetch(): Promise<void> {
    if (loaded.value || inFlight.value) return
    inFlight.value = true
    try {
      const caps = await getCapabilities()
      visionEnabled.value = caps.vision_enabled
      sunatEnabled.value = caps.sunat_enabled
      loaded.value = true
    } catch {
      // Fail safe: keep vision/sunat disabled. Do NOT set loaded — a later
      // retry is allowed. The gated controls stay disabled (REV-R35-S01).
      visionEnabled.value = false
      sunatEnabled.value = false
    } finally {
      inFlight.value = false
    }
  }

  return {
    // State
    visionEnabled,
    sunatEnabled,
    loaded,
    // Actions
    fetch,
  }
})
