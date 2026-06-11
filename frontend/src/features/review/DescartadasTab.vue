<template>
  <section class="descartadas-tab" aria-labelledby="descartadas-heading">
    <h2 id="descartadas-heading" class="descartadas-tab__heading">
      Descartadas para revisión
    </h2>

    <p
      v-if="discardedPages.length === 0"
      class="descartadas-tab__empty"
      role="status"
    >
      Sin páginas descartadas para esta ejecución.
    </p>

    <template v-else>
      <p class="descartadas-tab__hint">
        Páginas clasificadas como guía sin evidencia QR. Selecciona las que
        correspondan a guías reales y recupéralas.
      </p>

      <!-- Global selection toolbar (REV-R29 / A3) -->
      <div class="descartadas-tab__toolbar">
        <button
          class="descartadas-tab__select-all"
          type="button"
          @click="toggleAll"
        >
          {{ allSelected ? 'Deseleccionar todas' : `Seleccionar todas (${discardedPages.length})` }}
        </button>
        <button
          class="descartadas-tab__bulk-btn"
          type="button"
          :disabled="selected.size === 0 || !BULK_FLOW_READY || undefined"
          :title="BULK_FLOW_READY
            ? 'Recuperar las páginas seleccionadas'
            : 'La recuperación en lote estará disponible próximamente'"
        >
          Recuperar {{ selected.size }} seleccionadas
        </button>
      </div>

      <!-- Contiguous page-run groups (A1), collapsed by default (A2) -->
      <div
        v-for="group in groups"
        :key="group.key"
        class="descartadas-tab__group"
      >
        <div class="descartadas-tab__group-header">
          <!-- Tri-state group checkbox — usable while collapsed (A3) -->
          <input
            class="descartadas-tab__group-checkbox"
            type="checkbox"
            :checked="groupState(group) === 'all'"
            :indeterminate="groupState(group) === 'some'"
            :aria-label="`Seleccionar ${group.pages.length} páginas del grupo ${rangeLabel(group)}`"
            @change="toggleGroup(group)"
          />
          <button
            class="descartadas-tab__group-toggle"
            type="button"
            :aria-expanded="isExpanded(group)"
            @click="toggleExpanded(group)"
          >
            <span class="descartadas-tab__group-caret" aria-hidden="true">
              {{ isExpanded(group) ? '▾' : '▸' }}
            </span>
            {{ rangeLabel(group) }}
          </button>
          <span
            class="descartadas-tab__group-count"
            :aria-label="`${group.pages.length} páginas descartadas en este grupo`"
          >
            {{ group.pages.length }}
          </span>
          <span class="descartadas-tab__group-registro">
            {{ group.registro !== null ? `Registro ${group.registro}` : 'Sin registro' }}
          </span>
        </div>

        <!-- Collapsed = v-if: zero <img> elements exist until expand (A2) -->
        <ul v-if="isExpanded(group)" class="descartadas-tab__pages">
          <li
            v-for="entry in group.pages"
            :key="entry.page"
            class="descartadas-tab__page"
          >
            <input
              class="descartadas-tab__page-checkbox"
              type="checkbox"
              :checked="selected.has(entry.page)"
              :aria-label="`Seleccionar página ${entry.page}`"
              @change="togglePage(entry.page)"
            />

            <!-- Lazy thumbnail — SourcePages pattern (loading=lazy + load/error Sets) -->
            <button
              class="descartadas-tab__thumb-btn"
              type="button"
              :title="`Ver página ${entry.page}`"
              :aria-label="`Ver página ${entry.page}`"
              @click="openViewer(entry.page)"
            >
              <img
                :src="thumbnailSrc(entry.page)"
                :alt="`Miniatura página ${entry.page}`"
                class="descartadas-tab__thumb"
                :class="{ 'descartadas-tab__thumb--hidden': !thumbnailsLoaded.has(entry.page) }"
                loading="lazy"
                @load="onThumbLoad(entry.page)"
                @error="onThumbError(entry.page)"
              />
              <span class="descartadas-tab__page-number">{{ entry.page }}</span>
            </button>

            <div class="descartadas-tab__page-meta">
              <span class="descartadas-tab__page-label">Página {{ entry.page }}</span>
              <span
                v-if="entry.has_cached_lines"
                class="descartadas-tab__cached-hint"
                title="Hay líneas OCR en caché — la recuperación es casi instantánea"
              >
                OCR en caché
              </span>
              <p
                v-if="pageErrors[entry.page]"
                class="descartadas-tab__page-error"
                role="alert"
              >
                {{ pageErrors[entry.page] }}
              </p>
            </div>

            <button
              class="descartadas-tab__recover-btn"
              type="button"
              :disabled="pendingPages.has(entry.page) || undefined"
              :aria-busy="pendingPages.has(entry.page)"
              :title="`Recuperar la página ${entry.page} como guía del registro`"
              @click="recoverPage(entry.page)"
            >
              {{ pendingPages.has(entry.page) ? 'Recuperando…' : 'Recuperar' }}
            </button>
          </li>
        </ul>
      </div>
    </template>

    <!-- Page-sheet viewer (PR#48 reuse): full-res scanned page lightbox -->
    <PageSheetViewer
      v-model="showViewer"
      :run-id="runId"
      :page="viewerPage"
      :row-pages="[viewerPage]"
    />
  </section>
</template>

<script setup lang="ts">
/**
 * DescartadasTab — [Descartadas para revisión] tab body (SDD#2 PR-3a).
 *
 * Surfaces GUIA-classified pages dropped by the rev-6 QR-evidence gate
 * (issue #50) so the operator can review and recover them. Structurally
 * separate from Pendientes/ErroredGuiasPanel (design D1 / REV-R33): no
 * REINTENTAR, no retry_attempted — only Recuperar actions.
 *
 * A1 — grouping: contiguous page-runs computed frontend-side (derived
 *   view-model, same pattern as Pendientes `groups`). A run breaks at a
 *   page-index gap OR a registro change; headers always show ONE registro.
 * A2 — rendering: groups collapsed by default (v-if body → zero <img> on
 *   tab open; 343 thumbnail requests avoided). On expand, the SourcePages
 *   lazy pattern is reused verbatim: <img loading="lazy"> + load/error Sets.
 * A3 — selection: per-page checkboxes + per-group tri-state header checkbox
 *   (usable collapsed) + global "Seleccionar todas (N)". Selection is
 *   ephemeral component state (REV-R29). The bulk action button renders
 *   disabled in PR-3a — PR-3b wires the ETA confirm dialog + batch fire.
 *
 * Single-page Recuperar (REV-R31 UI): POST /discarded-pages/{page}/recover.
 * recovered=true → emit('refetch') so the parent refreshes GET /table (the
 * entry leaves this list via the refreshed discarded_pages prop, and the
 * recovered row lands in Reconciliación flagged requires_review — the
 * validation gate, never auto-accepted). recovered=false → the reason is
 * shown honestly next to the entry; the entry stays.
 */

import { ref, reactive, computed } from 'vue'
import type { DiscardedPageResponse } from '@/api/types'
import { recoverDiscardedPage } from '@/api/client'
import PageSheetViewer from './PageSheetViewer.vue'

const props = defineProps<{
  /** Discarded entries from the table response (REV-R33, sorted or not). */
  discardedPages: DiscardedPageResponse[]
  /** Run ID — required for thumbnail URLs + recovery endpoints. */
  runId: string
  /** Base API URL (proxied in dev, VITE_API_BASE_URL in prod). */
  apiBase?: string
}>()

const emit = defineEmits<{
  /** Ask the parent to refetch GET /table after a successful recovery. */
  (e: 'refetch'): void
}>()

/**
 * PR-3a gate: the bulk batch flow (ETA confirm dialog + recover-batch fire +
 * poll-until-done) lands in PR-3b. The button is rendered (selection state is
 * consumed there) but stays disabled so no dead click path ships.
 */
const BULK_FLOW_READY = false

// ---------------------------------------------------------------------------
// A1 — grouping: contiguous page-runs, break on index gap OR registro change
// ---------------------------------------------------------------------------

interface DiscardedGroup {
  /** Stable key: first page of the run. */
  key: number
  /** Single registro for the whole run (A5 structural guarantee), or null. */
  registro: string | null
  pages: DiscardedPageResponse[]
}

const groups = computed<DiscardedGroup[]>(() => {
  const sorted = [...props.discardedPages].sort((a, b) => a.page - b.page)
  const out: DiscardedGroup[] = []
  let current: DiscardedGroup | null = null
  for (const entry of sorted) {
    const prev = current?.pages[current.pages.length - 1]
    if (
      current === null ||
      prev === undefined ||
      entry.page !== prev.page + 1 ||
      entry.registro !== current.registro
    ) {
      current = { key: entry.page, registro: entry.registro, pages: [entry] }
      out.push(current)
    } else {
      current.pages.push(entry)
    }
  }
  return out
})

function rangeLabel(group: DiscardedGroup): string {
  const first = group.pages[0].page
  const last = group.pages[group.pages.length - 1].page
  return first === last ? `Página ${first}` : `Páginas ${first}–${last}`
}

// ---------------------------------------------------------------------------
// A2 — expansion state (kept OUTSIDE the computed so it survives re-derives)
// ---------------------------------------------------------------------------

const expandedGroups = ref<Set<number>>(new Set())

function isExpanded(group: DiscardedGroup): boolean {
  return expandedGroups.value.has(group.key)
}

function toggleExpanded(group: DiscardedGroup): void {
  const next = new Set(expandedGroups.value)
  if (next.has(group.key)) next.delete(group.key)
  else next.add(group.key)
  expandedGroups.value = next
}

// ---------------------------------------------------------------------------
// A3 — selection: ephemeral Set<number> (REV-R29)
// ---------------------------------------------------------------------------

const selected = ref<Set<number>>(new Set())

function togglePage(page: number): void {
  const next = new Set(selected.value)
  if (next.has(page)) next.delete(page)
  else next.add(page)
  selected.value = next
}

type GroupSelectionState = 'all' | 'some' | 'none'

function groupState(group: DiscardedGroup): GroupSelectionState {
  let count = 0
  for (const entry of group.pages) {
    if (selected.value.has(entry.page)) count += 1
  }
  if (count === 0) return 'none'
  return count === group.pages.length ? 'all' : 'some'
}

/** Tri-state toggle: all selected → clear the run; otherwise select the run. */
function toggleGroup(group: DiscardedGroup): void {
  const next = new Set(selected.value)
  if (groupState(group) === 'all') {
    for (const entry of group.pages) next.delete(entry.page)
  } else {
    for (const entry of group.pages) next.add(entry.page)
  }
  selected.value = next
}

const allSelected = computed<boolean>(
  () =>
    props.discardedPages.length > 0 &&
    selected.value.size === props.discardedPages.length,
)

function toggleAll(): void {
  selected.value = allSelected.value
    ? new Set()
    : new Set(props.discardedPages.map((entry) => entry.page))
}

// ---------------------------------------------------------------------------
// Thumbnails — SourcePages pattern (load/error Sets, graceful degrade)
// ---------------------------------------------------------------------------

const thumbnailsLoaded = ref<Set<number>>(new Set())
const thumbnailsErrored = ref<Set<number>>(new Set())

function thumbnailSrc(page: number): string {
  const base = props.apiBase ?? '/api/v1'
  return `${base}/runs/${props.runId}/pages/${page}/thumbnail`
}

function onThumbLoad(page: number): void {
  thumbnailsLoaded.value = new Set([...thumbnailsLoaded.value, page])
  if (thumbnailsErrored.value.has(page)) {
    const next = new Set(thumbnailsErrored.value)
    next.delete(page)
    thumbnailsErrored.value = next
  }
}

function onThumbError(page: number): void {
  thumbnailsErrored.value = new Set([...thumbnailsErrored.value, page])
  if (thumbnailsLoaded.value.has(page)) {
    const next = new Set(thumbnailsLoaded.value)
    next.delete(page)
    thumbnailsLoaded.value = next
  }
}

// ---------------------------------------------------------------------------
// Page-sheet viewer (PR#48 reuse — REV-R28-S04)
// ---------------------------------------------------------------------------

const showViewer = ref(false)
const viewerPage = ref<number>(0)

function openViewer(page: number): void {
  viewerPage.value = page
  showViewer.value = true
}

// ---------------------------------------------------------------------------
// Single-page recovery (REV-R31 UI) — honest failure reasons, never silent
// ---------------------------------------------------------------------------

const pendingPages = ref<Set<number>>(new Set())
const pageErrors = reactive<Record<number, string>>({})

const FAILURE_REASONS: Record<string, string> = {
  empty:
    'No se pudo recuperar: OCR y visión no extrajeron líneas de material.',
  not_found: 'La página ya no está en la lista de descartadas.',
  already_recovered: 'Esta página ya fue recuperada anteriormente.',
}

async function recoverPage(page: number): Promise<void> {
  if (pendingPages.value.has(page)) return
  delete pageErrors[page]
  pendingPages.value = new Set([...pendingPages.value, page])

  try {
    const result = await recoverDiscardedPage(props.runId, page)
    if (result.recovered) {
      // The entry leaves the list via the parent's table refetch; the
      // recovered row appears in Reconciliación flagged requires_review.
      emit('refetch')
    } else {
      pageErrors[page] =
        FAILURE_REASONS[result.reason ?? ''] ??
        'No se pudo recuperar la página.'
    }
  } catch {
    pageErrors[page] =
      'Error al recuperar la página. Verifica la conexión e inténtalo de nuevo.'
  } finally {
    const next = new Set(pendingPages.value)
    next.delete(page)
    pendingPages.value = next
  }
}
</script>

<style scoped>
.descartadas-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.descartadas-tab__heading {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
}

.descartadas-tab__hint {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.descartadas-tab__empty {
  padding: var(--space-6) var(--space-4);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  background-color: var(--surface-raised);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-lg);
  text-align: center;
}

/* Global selection toolbar */
.descartadas-tab__toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.descartadas-tab__select-all {
  display: inline-flex;
  align-items: center;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-default);
  background-color: var(--surface-raised);
  color: var(--text-primary);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.descartadas-tab__select-all:hover {
  background-color: var(--surface-hover);
}

.descartadas-tab__select-all:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.descartadas-tab__bulk-btn {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-primary, #4f46e5);
  background-color: var(--surface-raised);
  color: var(--color-primary, #4f46e5);
  cursor: pointer;
  transition: background-color var(--transition-fast), opacity var(--transition-fast);
  white-space: nowrap;
}

.descartadas-tab__bulk-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.descartadas-tab__bulk-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

/* Group (contiguous page-run) */
.descartadas-tab__group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.descartadas-tab__group-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background-color: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
}

.descartadas-tab__group-checkbox,
.descartadas-tab__page-checkbox {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  cursor: pointer;
  accent-color: var(--color-primary, #4f46e5);
}

.descartadas-tab__group-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}

.descartadas-tab__group-toggle:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
  border-radius: var(--radius-sm);
}

.descartadas-tab__group-caret {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.descartadas-tab__group-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 var(--space-1);
  border-radius: var(--radius-pill);
  background-color: var(--status-mismatch-bg, #fde8e8);
  color: var(--status-mismatch-fg, #c0392b);
  font-size: var(--text-xs);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.descartadas-tab__group-registro {
  margin-left: auto;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
}

/* Expanded page list */
.descartadas-tab__pages {
  list-style: none;
  margin: 0;
  padding: 0 0 0 var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.descartadas-tab__page {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
}

.descartadas-tab__thumb-btn {
  position: relative;
  width: 48px;
  height: 48px;
  padding: 0;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-default);
  background-color: var(--surface-inset);
  cursor: pointer;
  overflow: hidden;
  flex-shrink: 0;
}

.descartadas-tab__thumb-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.descartadas-tab__thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* Hidden until @load fires — avoids broken-image icon flash */
.descartadas-tab__thumb--hidden {
  display: none;
}

.descartadas-tab__page-number {
  position: absolute;
  bottom: 1px;
  right: 1px;
  min-width: 12px;
  padding: 0 3px;
  border-radius: var(--radius-sm);
  background-color: rgba(0, 0, 0, 0.78);
  color: #fff;
  font-size: 0.5625rem;
  font-weight: 600;
  line-height: 1.45;
  text-align: center;
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}

.descartadas-tab__page-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.descartadas-tab__page-label {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-primary);
}

.descartadas-tab__cached-hint {
  font-size: var(--text-2xs);
  font-weight: 600;
  color: var(--status-match-fg, #1a7f37);
}

.descartadas-tab__page-error {
  font-size: var(--text-xs);
  color: var(--status-mismatch-fg, #c0392b);
}

.descartadas-tab__recover-btn {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-primary, #4f46e5);
  background-color: var(--surface-raised);
  color: var(--color-primary, #4f46e5);
  cursor: pointer;
  transition: background-color var(--transition-fast), opacity var(--transition-fast);
  white-space: nowrap;
}

.descartadas-tab__recover-btn:hover:not(:disabled) {
  background-color: var(--surface-hover);
}

.descartadas-tab__recover-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.descartadas-tab__recover-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
