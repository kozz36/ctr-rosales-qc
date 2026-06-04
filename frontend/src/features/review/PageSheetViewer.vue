<template>
  <Teleport to="body">
    <div
      v-if="modelValue"
      class="page-viewer__backdrop"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      @click.self="close"
      @keydown.escape="close"
      @keydown.left="goPrev"
      @keydown.right="goNext"
      @keydown.r="rotateCw"
      @keydown.+="zoomIn"
      @keydown.-="zoomOut"
      @keydown.0="resetTransform"
    >
      <div ref="dialogRef" class="page-viewer" tabindex="-1">
        <!-- Header -->
        <header class="page-viewer__header">
          <h2 :id="titleId" class="page-viewer__title">
            <span class="page-viewer__title-label">Página de origen</span>
            <span class="page-viewer__page-badge mono">{{ activePage }}</span>
            <span v-if="hasSiblings" class="page-viewer__position mono">
              {{ siblingIndex + 1 }} / {{ siblingPages.length }}
            </span>
          </h2>
          <div class="page-viewer__actions">
            <!-- Zoom + rotate toolbar -->
            <div class="page-viewer__toolbar" role="group" aria-label="Controles de imagen">
              <button
                type="button"
                class="page-viewer__tool page-viewer__tool--zoom-out"
                aria-label="Alejar"
                :disabled="zoom <= MIN_ZOOM"
                @click="zoomOut"
              >
                <span aria-hidden="true">−</span>
              </button>
              <span class="page-viewer__zoom-label mono" aria-live="polite">
                {{ Math.round(zoom * 100) }}%
              </span>
              <button
                type="button"
                class="page-viewer__tool page-viewer__tool--zoom-in"
                aria-label="Acercar"
                :disabled="zoom >= MAX_ZOOM"
                @click="zoomIn"
              >
                <span aria-hidden="true">+</span>
              </button>
              <button
                type="button"
                class="page-viewer__tool page-viewer__tool--rotate"
                aria-label="Rotar 90 grados"
                @click="rotateCw"
              >
                <span aria-hidden="true">⟳</span>
              </button>
              <button
                type="button"
                class="page-viewer__tool page-viewer__tool--reset"
                aria-label="Restablecer zoom y rotación"
                :disabled="zoom === 1 && rotation === 0"
                @click="resetTransform"
              >
                <span aria-hidden="true">⤢</span>
              </button>
            </div>

            <button
              ref="closeRef"
              type="button"
              class="page-viewer__close"
              aria-label="Cerrar visor de página"
              @click="close"
            >
              <span aria-hidden="true">✕</span>
            </button>
          </div>
        </header>

        <!-- Image stage -->
        <div class="page-viewer__stage">
          <button
            v-if="hasSiblings"
            type="button"
            class="page-viewer__nav page-viewer__nav--prev"
            aria-label="Página anterior"
            :disabled="siblingIndex <= 0"
            @click="goPrev"
          >
            <span aria-hidden="true">‹</span>
          </button>

          <div class="page-viewer__frame">
            <div v-if="loading" class="page-viewer__spinner" role="status" aria-live="polite">
              <span class="page-viewer__spinner-dot" aria-hidden="true" />
              <span class="page-viewer__spinner-text">Cargando página…</span>
            </div>
            <div v-if="errored" class="page-viewer__error" role="alert">
              <span aria-hidden="true">⚠</span>
              No se pudo cargar la imagen de la página {{ activePage }}.
            </div>
            <img
              v-show="!loading && !errored"
              :key="activePage"
              :src="imageSrc"
              :alt="`Página de origen ${activePage} (resolución completa)`"
              class="page-viewer__image"
              :class="{ 'page-viewer__image--zoomed': zoom > 1 }"
              :style="imageTransform"
              @load="onLoad"
              @error="onError"
            />
          </div>

          <button
            v-if="hasSiblings"
            type="button"
            class="page-viewer__nav page-viewer__nav--next"
            aria-label="Página siguiente"
            :disabled="siblingIndex >= siblingPages.length - 1"
            @click="goNext"
          >
            <span aria-hidden="true">›</span>
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * PageSheetViewer — full-resolution scanned-page lightbox (issue #27).
 *
 * Opens on a SourcePages chip click and renders the full page via
 * GET /runs/{id}/pages/{page}/image (200 DPI, distinct cache from the thumbnail).
 *
 * A11y: role="dialog" + aria-modal, focus moves to the dialog on open, ESC and
 * backdrop click close, a visible close button is provided. Optional prev/next
 * navigation walks the SAME row's source pages (rowPages) when supplied — a
 * cheap convenience that never fetches outside the row context.
 */

import { ref, computed, watch, nextTick } from 'vue'

const props = defineProps<{
  /** Controls visibility (v-model). */
  modelValue: boolean
  /** The run_id — required to build the full-res image URL. */
  runId: string
  /** The page to display (1-based, as returned by the backend). */
  page: number
  /**
   * Optional: all source pages of the row the chip belongs to, enabling
   * prev/next navigation across that row only. When absent, nav is hidden.
   */
  rowPages?: number[]
  /** Base API URL (proxied in dev, VITE_API_BASE_URL in prod). */
  apiBase?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const dialogRef = ref<HTMLDivElement | null>(null)
const closeRef = ref<HTMLButtonElement | null>(null)

// Local active page so prev/next can move without round-tripping through the parent.
const activePage = ref<number>(props.page)
const loading = ref(true)
const errored = ref(false)

const siblingPages = computed<number[]>(() => props.rowPages ?? [])
const hasSiblings = computed(() => siblingPages.value.length > 1)
const siblingIndex = computed(() => siblingPages.value.indexOf(activePage.value))

const titleId = `page-viewer-title-${Math.random().toString(36).slice(2, 8)}`

const imageSrc = computed(() => {
  const base = props.apiBase ?? '/api/v1'
  return `${base}/runs/${props.runId}/pages/${activePage.value}/image`
})

// ---------------------------------------------------------------------------
// Zoom + rotate (client-side CSS transform — no extra network, no domain touch)
// ---------------------------------------------------------------------------

const MIN_ZOOM = 1
const MAX_ZOOM = 4
const ZOOM_STEP = 0.5

const zoom = ref(MIN_ZOOM)
/** Rotation in degrees, always normalized to 0/90/180/270. */
const rotation = ref(0)

// scale() + rotate() in a single transform; identity baseline is scale(1) rotate(0deg)
// so the reset assertion (and visual reset) is explicit, not an empty string.
const imageTransform = computed(() => ({
  transform: `scale(${zoom.value}) rotate(${rotation.value}deg)`,
}))

function zoomIn(): void {
  zoom.value = Math.min(MAX_ZOOM, Math.round((zoom.value + ZOOM_STEP) * 100) / 100)
}

function zoomOut(): void {
  zoom.value = Math.max(MIN_ZOOM, Math.round((zoom.value - ZOOM_STEP) * 100) / 100)
}

function rotateCw(): void {
  rotation.value = (rotation.value + 90) % 360
}

function resetTransform(): void {
  zoom.value = MIN_ZOOM
  rotation.value = 0
}

function onLoad(): void {
  loading.value = false
  errored.value = false
}

function onError(): void {
  loading.value = false
  errored.value = true
}

function resetLoad(): void {
  loading.value = true
  errored.value = false
  // A new page starts at the identity transform — stale zoom/rotation would
  // disorient the operator when navigating across a row's pages.
  resetTransform()
}

function goPrev(): void {
  if (!hasSiblings.value) return
  const i = siblingIndex.value
  if (i > 0) {
    resetLoad()
    activePage.value = siblingPages.value[i - 1]
  }
}

function goNext(): void {
  if (!hasSiblings.value) return
  const i = siblingIndex.value
  if (i >= 0 && i < siblingPages.value.length - 1) {
    resetLoad()
    activePage.value = siblingPages.value[i + 1]
  }
}

function close(): void {
  emit('update:modelValue', false)
}

// Sync active page + reset load state, and move focus into the dialog on open.
watch(
  () => [props.modelValue, props.page] as const,
  async ([open]) => {
    if (open) {
      activePage.value = props.page
      resetLoad()
      await nextTick()
      dialogRef.value?.focus()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.page-viewer__backdrop {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgba(0, 0, 0, 0.78);
  backdrop-filter: blur(3px);
  padding: var(--space-4);
  animation: pv-fade var(--transition-normal) ease;
}

@keyframes pv-fade {
  from { opacity: 0; }
  to   { opacity: 1; }
}

.page-viewer {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: min(900px, 92vw);
  max-height: 92vh;
  background-color: var(--surface-overlay);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  animation: pv-rise var(--transition-normal) ease;
}

.page-viewer:focus {
  outline: none;
}

@keyframes pv-rise {
  from { transform: translateY(14px); opacity: 0; }
  to   { transform: translateY(0);    opacity: 1; }
}

/* Header */
.page-viewer__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border-subtle);
  background-color: var(--surface-inset);
}

.page-viewer__title {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.page-viewer__title-label {
  letter-spacing: 0.02em;
}

.page-viewer__page-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  background-color: var(--surface-overlay);
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.page-viewer__position {
  color: var(--text-tertiary);
  font-size: var(--text-2xs);
  font-family: var(--font-mono);
}

/* Header actions: zoom/rotate toolbar + close */
.page-viewer__actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.page-viewer__toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-subtle);
  background-color: var(--surface-overlay);
}

.page-viewer__tool {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  border: none;
  background: none;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: 1;
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast),
    opacity var(--transition-fast);
}

.page-viewer__tool:not(:disabled):hover {
  background-color: var(--surface-hover);
  color: var(--text-primary);
}

.page-viewer__tool:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.page-viewer__tool:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.page-viewer__zoom-label {
  min-width: 38px;
  text-align: center;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  user-select: none;
}

.page-viewer__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  border: none;
  background: none;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.page-viewer__close:hover {
  background-color: var(--surface-hover);
  color: var(--text-primary);
}

.page-viewer__close:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

/* Stage */
.page-viewer__stage {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4);
  min-height: 0;
  flex: 1;
  /* Checkerboard-free neutral mat so a white scanned sheet reads against the frame. */
  background-color: var(--surface-base);
}

.page-viewer__frame {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
  min-width: 0;
  /* When zoomed past the frame, let the operator pan via scroll. */
  overflow: auto;
}

.page-viewer__image {
  max-width: 100%;
  max-height: calc(92vh - 120px);
  object-fit: contain;
  border-radius: var(--radius-sm);
  box-shadow: 0 4px 18px 0 rgb(0 0 0 / 0.5);
  background-color: #fff;
  transform-origin: center center;
  transition: transform var(--transition-fast) ease;
}

.page-viewer__image--zoomed {
  cursor: zoom-out;
}

@media (prefers-reduced-motion: reduce) {
  .page-viewer__image {
    transition: none;
  }
}

.page-viewer__spinner,
.page-viewer__error {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-secondary);
  font-size: var(--text-sm);
}

.page-viewer__error {
  color: var(--status-mismatch-fg);
}

.page-viewer__spinner-dot {
  width: 14px;
  height: 14px;
  border: 2px solid var(--border-default);
  border-top-color: var(--text-primary);
  border-radius: 50%;
  animation: pv-spin 0.7s linear infinite;
}

@keyframes pv-spin {
  to { transform: rotate(360deg); }
}

/* Nav arrows */
.page-viewer__nav {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-default);
  background-color: var(--surface-overlay);
  color: var(--text-secondary);
  font-size: var(--text-lg);
  line-height: 1;
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast),
    opacity var(--transition-fast);
}

.page-viewer__nav:not(:disabled):hover {
  background-color: var(--surface-hover);
  border-color: var(--border-strong);
  color: var(--text-primary);
}

.page-viewer__nav:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.page-viewer__nav:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
</style>
