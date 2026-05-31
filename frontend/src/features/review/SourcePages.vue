<template>
  <div class="source-pages" :aria-label="`Páginas de origen: ${pages.join(', ')}`">
    <button
      v-for="page in pages"
      :key="page"
      class="source-pages__chip"
      :class="{ 'source-pages__chip--has-thumb': thumbnailsLoaded.has(page) }"
      :title="`Página ${page}${thumbnailsLoaded.has(page) ? '' : thumbnailsErrored.has(page) ? ' (miniatura no disponible)' : ' (cargando...)'}`"
      :aria-label="`Ver página ${page}`"
      tabindex="0"
      @click="onChipClick(page)"
      @keydown.enter="onChipClick(page)"
      @keydown.space.prevent="onChipClick(page)"
    >
      <!-- Thumbnail (enhancement) — degrades gracefully on 404 -->
      <img
        v-if="thumbnailsLoaded.has(page)"
        :src="thumbnailSrc(page)"
        :alt="`Miniatura página ${page}`"
        class="source-pages__thumb"
        loading="lazy"
      />
      <!-- Fallback: page number chip -->
      <span class="source-pages__number" :aria-hidden="thumbnailsLoaded.has(page)">
        {{ page }}
      </span>
    </button>

    <!-- Thumbnail missing endpoint notice (dev only, NOT a user-facing error) -->
    <!-- BACKEND FLAG: GET /runs/{run_id}/pages/{page}/thumbnail not yet implemented.
         See PR-5b return: missing-thumbnail-endpoint flag. -->
  </div>
</template>

<script setup lang="ts">
/**
 * SourcePages — renders source page numbers as interactive chips.
 *
 * Thumbnail enhancement (OPTIONAL, degrades gracefully):
 *   Attempts GET /runs/{run_id}/pages/{page}/thumbnail via an <img> src load.
 *   On 404 / network error → falls back to the page-number chip.
 *
 * BACKEND FLAG: The thumbnail endpoint (GET /runs/{run_id}/pages/{page}/thumbnail)
 * does NOT exist yet. The component's graceful degradation ensures this is a
 * non-breaking gap: users see page-number chips until the backend implements it.
 * When the endpoint is implemented, no frontend change is needed.
 */

import { ref, onMounted, watch } from 'vue'

const props = defineProps<{
  /** Source page numbers (1-based, as returned by the backend). */
  pages: number[]
  /** The run_id — required to build thumbnail URLs. */
  runId: string
  /** Base API URL (proxied in dev, VITE_API_BASE_URL in prod). */
  apiBase?: string
}>()

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

/**
 * Probe each page's thumbnail endpoint by attempting an image load.
 * This fires once per page and degrades silently on failure.
 */
function probeThumbnails(): void {
  for (const page of props.pages) {
    // Skip if already resolved
    if (thumbnailsLoaded.value.has(page) || thumbnailsErrored.value.has(page)) continue

    const img = new Image()
    img.src = thumbnailSrc(page)
    img.onload = () => {
      thumbnailsLoaded.value = new Set([...thumbnailsLoaded.value, page])
    }
    img.onerror = () => {
      thumbnailsErrored.value = new Set([...thumbnailsErrored.value, page])
    }
  }
}

onMounted(probeThumbnails)
watch(() => props.pages, probeThumbnails, { deep: true })

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
</style>
