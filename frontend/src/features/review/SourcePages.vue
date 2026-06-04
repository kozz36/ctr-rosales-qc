<template>
  <div class="source-pages" :aria-label="`Páginas de origen: ${pages.join(', ')}`">
    <button
      v-for="page in pages"
      :key="page"
      class="source-pages__chip"
      :class="{
        'source-pages__chip--has-thumb': thumbnailsLoaded.has(page),
        'source-pages__chip--divergent': resolvedDivergentPages.has(page),
      }"
      :title="resolvedDivergentPages.has(page)
        ? `Página ${page} — Revisar guía: fecha divergente`
        : `Página ${page}${thumbnailsLoaded.has(page) ? '' : thumbnailsErrored.has(page) ? ' (miniatura no disponible)' : ' (cargando...)'}`"
      :aria-label="resolvedDivergentPages.has(page) ? `Ver página ${page} — fecha divergente, requiere revisión` : `Ver página ${page}`"
      tabindex="0"
      @click="onChipClick(page)"
      @keydown.enter="onChipClick(page)"
      @keydown.space.prevent="onChipClick(page)"
    >
      <!-- Thumbnail (S1.8 endpoint — degrades gracefully on error).
           The <img> is always rendered; onerror moves the page to thumbnailsErrored
           and removes the has-thumb class so the fallback chip shows instead. -->
      <img
        :src="thumbnailSrc(page)"
        :alt="`Miniatura página ${page}`"
        class="source-pages__thumb"
        :class="{ 'source-pages__thumb--hidden': !thumbnailsLoaded.has(page) }"
        loading="lazy"
        @load="onThumbLoad(page)"
        @error="onThumbError(page)"
      />
      <!-- Fallback: page number chip (shown when thumbnail not loaded or errored) -->
      <span class="source-pages__number" :aria-hidden="thumbnailsLoaded.has(page)">
        {{ page }}
      </span>
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * SourcePages — renders source page numbers as interactive chips.
 *
 * Thumbnail enhancement (S2.5 / S1.8 — REV-005):
 *   Uses a declarative <img :src="thumbnailUrl"> pointing to the API thumbnail
 *   endpoint (GET /api/runs/{id}/pages/{page}/thumbnail). The @load handler
 *   marks the page as loaded; @error marks it as errored and degrades gracefully
 *   to the page-number chip.
 *
 *   This replaces the old imperative `new Image()` probe approach (S2.5 cleanup).
 */

import { computed, ref } from 'vue'

const props = defineProps<{
  /** Source page numbers (1-based, as returned by the backend). */
  pages: number[]
  /** The run_id — required to build thumbnail URLs. */
  runId: string
  /** Base API URL (proxied in dev, VITE_API_BASE_URL in prod). */
  apiBase?: string
  /**
   * FIX #14 (R9 fecha-divergence red highlight): set of page numbers whose guía
   * has fecha_divergence=true. Chips for these pages receive the --divergent class
   * and an accessible title hint. Purely additive display side-channel — never
   * affects reconciliation logic, group key, or MATCH status.
   */
  divergentPages?: Set<number>
}>()

// Resolved divergent set — defaults to empty so the template never needs a null check.
const resolvedDivergentPages = computed(() => props.divergentPages ?? new Set<number>())

const emit = defineEmits<{
  /** Emitted when a chip is clicked — parent can open a lightbox/modal. */
  pageClick: [page: number]
}>()

// Track per-page thumbnail state
const thumbnailsLoaded = ref<Set<number>>(new Set())
const thumbnailsErrored = ref<Set<number>>(new Set())

function thumbnailSrc(page: number): string {
  const base = props.apiBase ?? '/api/v1'
  return `${base}/runs/${props.runId}/pages/${page}/thumbnail`
}

function onThumbLoad(page: number): void {
  thumbnailsLoaded.value = new Set([...thumbnailsLoaded.value, page])
  // Remove from errored set if it was there (e.g. after retry)
  if (thumbnailsErrored.value.has(page)) {
    const next = new Set(thumbnailsErrored.value)
    next.delete(page)
    thumbnailsErrored.value = next
  }
}

function onThumbError(page: number): void {
  thumbnailsErrored.value = new Set([...thumbnailsErrored.value, page])
  // Remove from loaded set
  if (thumbnailsLoaded.value.has(page)) {
    const next = new Set(thumbnailsLoaded.value)
    next.delete(page)
    thumbnailsLoaded.value = next
  }
}

function onChipClick(page: number): void {
  emit('pageClick', page)
}
</script>

<style scoped>
.source-pages {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  align-items: center;
}

.source-pages__chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 24px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-default);
  background-color: var(--surface-inset);
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast),
    color var(--transition-fast);
  overflow: hidden;
  position: relative;
}

.source-pages__chip:hover,
.source-pages__chip:focus-visible {
  border-color: var(--border-focus);
  background-color: var(--surface-hover);
  color: var(--text-primary);
}

.source-pages__chip:focus-visible {
  box-shadow: var(--shadow-focus);
  outline: none;
}

.source-pages__chip--has-thumb {
  width: 32px;
  height: 32px;
  padding: 0;
  border-radius: var(--radius-md);
}

.source-pages__thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* Hidden until @load fires — avoids broken-image icon flash */
.source-pages__thumb--hidden {
  display: none;
}

.source-pages__number {
  line-height: 1;
}

/* When chip has thumb, position number as overlay */
.source-pages__chip--has-thumb .source-pages__number {
  position: absolute;
  bottom: 2px;
  right: 3px;
  font-size: 0.5rem;
  color: white;
  text-shadow: 0 0 4px rgba(0, 0, 0, 0.8);
}

/* FIX #14 (R9 fecha-divergence): red ring/glow on chips whose page belongs to a
   divergent guía. Uses the same mismatch/danger design tokens as FechaDivergenceBadge.
   Purely additive display — never affects reconciliation logic. */
.source-pages__chip--divergent {
  border-color: var(--status-mismatch-glow);
  color: var(--status-mismatch-fg);
  box-shadow: 0 0 0 2px var(--status-mismatch-glow);
}

.source-pages__chip--divergent:hover,
.source-pages__chip--divergent:focus-visible {
  border-color: var(--status-mismatch-fg);
  background-color: var(--status-mismatch-bg);
}
</style>
