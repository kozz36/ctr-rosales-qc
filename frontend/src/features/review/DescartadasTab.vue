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
          :disabled="selectedLive.length === 0 || batchInFlight || undefined"
          :title="batchInFlight
            ? 'Hay una recuperación en lote en curso'
            : 'Recuperar las páginas seleccionadas'"
          @click="openBulkConfirm($event)"
        >
          {{ batchInFlight ? 'Recuperando…' : `Recuperar ${selectedLive.length} seleccionadas` }}
        </button>
      </div>

      <!-- Batch progress (REV-R30-S02): live counts while in-flight; the
           completion summary renders ONLY after the backend reports done=true
           (PR#49 SA-5 lesson — never settle on a timing heuristic). -->
      <p
        v-if="batch !== null"
        class="descartadas-tab__batch-progress"
        role="status"
        aria-live="polite"
      >
        Recuperando {{ batch.total }} páginas… {{ batch.lastRecovered }}
        recuperadas / {{ batch.lastFailed }} fallidas hasta ahora.
      </p>
      <p
        v-else-if="batchSummary !== null"
        class="descartadas-tab__batch-summary"
        role="status"
      >
        Recuperación en lote completada:
        {{ batchSummary.recovered }}
        {{ batchSummary.recovered === 1 ? 'recuperada' : 'recuperadas' }} /
        {{ batchSummary.failed }}
        {{ batchSummary.failed === 1 ? 'falló' : 'fallaron' }}.
      </p>
      <p
        v-if="batchError !== null"
        class="descartadas-tab__batch-error"
        role="alert"
      >
        {{ batchError }}
      </p>

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
              :disabled="pendingPages.has(entry.page) || batchInFlight || undefined"
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

    <!-- A3 ETA confirm dialog (REV-R30-S01): count prominent + approximate
         ETA + conditional vision-cost warning. Mirrors the Pendientes dialog
         (focus trap + W2 focus restore). -->
    <div
      v-if="showBulkConfirm"
      class="descartadas-tab__dialog-backdrop"
      @click.self="cancelBulkConfirm"
    >
      <div
        ref="dialogEl"
        class="descartadas-tab__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="descartadas-confirm-title"
        @keydown.esc="cancelBulkConfirm"
        @keydown.tab="onDialogTab"
      >
        <h3 id="descartadas-confirm-title" class="descartadas-tab__dialog-title">
          ¿Recuperar {{ selectedLive.length }}
          {{ selectedLive.length === 1 ? 'página' : 'páginas' }}?
        </h3>
        <p class="descartadas-tab__dialog-body">
          OCR-primero, IA como último recurso.
        </p>
        <p class="descartadas-tab__dialog-eta">{{ etaLabel }}</p>
        <p
          v-if="ocrEmptyCount > 0"
          class="descartadas-tab__dialog-warning"
        >
          {{ ocrEmptyCount }}
          {{ ocrEmptyCount === 1
            ? 'página seleccionada no tiene'
            : 'páginas seleccionadas no tienen' }}
          OCR en caché: la recuperación puede recurrir a visión (IA) como
          último recurso, con posibles llamadas cloud.
        </p>
        <div class="descartadas-tab__dialog-actions">
          <button
            class="descartadas-tab__dialog-cancel"
            @click="cancelBulkConfirm"
          >
            Cancelar
          </button>
          <button
            ref="confirmBtnEl"
            class="descartadas-tab__dialog-confirm"
            @click="confirmBulkRecover"
          >
            Confirmar ({{ selectedLive.length }})
          </button>
        </div>
      </div>
    </div>
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
 *   ephemeral component state (REV-R29). Every selection consumer reads the
 *   `selectedLive` intersection against the current prop, so pages recovered
 *   elsewhere (single-page recover, another batch) never reach a payload.
 *
 * Bulk recovery (PR-3b — REV-R30 / A3 / A4): "Recuperar seleccionadas" →
 *   ETA confirm dialog (count prominent; ≈ K × 10 s approximate; conditional
 *   vision-cost warning when K OCR-empty pages > 0) → POST /recover-batch
 *   (202) → poll GET /recover-status until the backend reports `done:true` —
 *   the REAL completion signal (PR#49 SA-5 lesson: never settle on a timing
 *   heuristic). Progress is incremental ('refetch' per advance); failed pages
 *   stay listed; completion shows the real "N recuperadas / M fallaron".
 *   On mount, the status is polled once and an in-flight batch re-attached.
 *
 * Single-page Recuperar (REV-R31 UI): POST /discarded-pages/{page}/recover.
 * recovered=true → emit('refetch') so the parent refreshes GET /table (the
 * entry leaves this list via the refreshed discarded_pages prop, and the
 * recovered row lands in Reconciliación flagged requires_review — the
 * validation gate, never auto-accepted). recovered=false → the reason is
 * shown honestly next to the entry; the entry stays.
 */

import {
  ref,
  reactive,
  computed,
  watch,
  onMounted,
  onBeforeUnmount,
  nextTick,
} from 'vue'
import type {
  DiscardedPageResponse,
  DiscardedRecoverStatusResponse,
} from '@/api/types'
import {
  recoverDiscardedPage,
  recoverDiscardedBatch,
  getDiscardedRecoverStatus,
} from '@/api/client'
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

const POLL_INTERVAL_MS = 2000

/**
 * Failsafe hard-cap per page (PR#49 SA-5 pattern). The PRIMARY completion
 * signal is the backend `done` flag from GET /recover-status; this cap ONLY
 * guards against a hung batch that never reports done (generous: 30 s/page —
 * Tier-2 OCR is ~10 s/page; Tier-1 cached pages are near-instant).
 */
const HARD_CAP_PER_PAGE_MS = 30_000

/** Tier-2 OCR cost used for the A3 approximate ETA (design A3). */
const OCR_SECONDS_PER_PAGE = 10

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

/**
 * Live selection = `selected` ∩ current `discardedPages` (ctr-reviewer
 * carry-over fix): a single-page recover removes the entry from the parent's
 * refreshed prop but does NOT prune the `selected` Set, so every consumer of
 * the selection (bulk button count, dialog count, ETA, the recover-batch
 * payload) reads THIS intersection — stale page numbers never reach the
 * backend.
 */
const selectedLive = computed<number[]>(() => {
  const live = new Set(props.discardedPages.map((entry) => entry.page))
  return [...selected.value].filter((page) => live.has(page)).sort((a, b) => a - b)
})

/** Prune the raw Set at the source whenever the list refreshes. */
watch(
  () => props.discardedPages,
  (pages) => {
    const live = new Set(pages.map((entry) => entry.page))
    if ([...selected.value].some((page) => !live.has(page))) {
      selected.value = new Set(
        [...selected.value].filter((page) => live.has(page)),
      )
    }
  },
)

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

// ---------------------------------------------------------------------------
// PR-3b — A3 ETA confirm dialog (REV-R30-S01; Pendientes dialog pattern)
// ---------------------------------------------------------------------------

const showBulkConfirm = ref(false)
const dialogEl = ref<HTMLElement | null>(null)
const confirmBtnEl = ref<HTMLElement | null>(null)
/** W2: element that opened the dialog — focus is restored here on close. */
const triggerEl = ref<HTMLElement | null>(null)

/** K = selected pages WITHOUT cached OCR lines (Tier-2/3 candidates). */
const ocrEmptyCount = computed<number>(() => {
  const byPage = new Map(props.discardedPages.map((entry) => [entry.page, entry]))
  return selectedLive.value.filter(
    (page) => byPage.get(page)?.has_cached_lines === false,
  ).length
})

/**
 * A3 ETA line — ALWAYS labeled approximate. Only OCR-empty pages cost time
 * (~10 s each); cached-lines pages are near-instant Tier-1.
 */
const etaLabel = computed<string>(() => {
  const k = ocrEmptyCount.value
  if (k === 0) {
    return 'Todas las páginas seleccionadas tienen OCR en caché: recuperación casi instantánea.'
  }
  const minutes = Math.max(1, Math.ceil((k * OCR_SECONDS_PER_PAGE) / 60))
  return `Tiempo estimado: ≈ ${minutes} min (aproximado, ~10 s por página sin OCR en caché; las páginas con OCR en caché son casi instantáneas).`
})

function openBulkConfirm(event?: Event): void {
  if (batchInFlight.value || selectedLive.value.length === 0) return
  batchSummary.value = null
  batchError.value = null
  // W2: remember the trigger so focus can be restored on close.
  triggerEl.value = (event?.currentTarget as HTMLElement | null) ?? null
  showBulkConfirm.value = true
  void nextTick(() => confirmBtnEl.value?.focus())
}

/** W2: restore focus to the element that opened the dialog. */
function restoreTriggerFocus(): void {
  const el = triggerEl.value
  triggerEl.value = null
  void nextTick(() => el?.focus())
}

function cancelBulkConfirm(): void {
  showBulkConfirm.value = false
  restoreTriggerFocus()
}

/**
 * W2: focus-trap. Tab/Shift+Tab cycles within the dialog's focusable elements
 * instead of escaping to the page behind the modal (WAI-ARIA dialog pattern).
 */
function onDialogTab(event: KeyboardEvent): void {
  const root = dialogEl.value
  if (!root) return
  const focusable = [
    ...root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    ),
  ].filter((el) => !el.hasAttribute('disabled'))
  if (focusable.length === 0) return

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const active = document.activeElement as HTMLElement | null

  if (event.shiftKey) {
    if (active === first || !root.contains(active)) {
      event.preventDefault()
      last.focus()
    }
  } else if (active === last || !root.contains(active)) {
    event.preventDefault()
    first.focus()
  }
}

// ---------------------------------------------------------------------------
// PR-3b — batch fire + poll-until-done (REV-R30 / A4).
// Settlement is driven EXCLUSIVELY by the backend `done:true` signal from
// GET /recover-status (PR#49 SA-5 lesson — NEVER a timing heuristic). The
// per-page hard cap below is ONLY a failsafe against a hung batch.
// ---------------------------------------------------------------------------

interface BulkBatchState {
  /** Pages submitted (or, on re-attach, the backend-reported total). */
  total: number
  /** Date.now() when polling started (failsafe-cap anchor). */
  startedAt: number
  /** Failsafe hard cap on total poll duration (∝ total). */
  capMs: number
  timer: ReturnType<typeof setInterval> | null
  /** Last-known REAL backend counts (drive the live progress line). */
  lastRecovered: number
  lastFailed: number
}

const batch = ref<BulkBatchState | null>(null)
const batchInFlight = computed<boolean>(() => batch.value !== null)
const batchSummary = ref<{ recovered: number; failed: number } | null>(null)
const batchError = ref<string | null>(null)

async function confirmBulkRecover(): Promise<void> {
  showBulkConfirm.value = false
  restoreTriggerFocus()
  if (batchInFlight.value) return

  // Intersection AGAIN at fire time (carry-over fix, defense-in-depth): only
  // pages still present in the discarded list reach the payload.
  const pages = selectedLive.value
  if (pages.length === 0) return

  batch.value = {
    total: pages.length,
    startedAt: Date.now(),
    capMs: Math.max(1, pages.length) * HARD_CAP_PER_PAGE_MS,
    timer: null,
    lastRecovered: 0,
    lastFailed: 0,
  }

  try {
    await recoverDiscardedBatch(props.runId, pages)
  } catch (err: unknown) {
    batch.value = null
    const status = (err as { response?: { status?: number } })?.response?.status
    batchError.value =
      status === 409
        ? 'Ya hay una recuperación en lote en curso para esta ejecución.'
        : 'No se pudo iniciar la recuperación en lote. Inténtalo de nuevo.'
    return
  }

  startBatchPolling({ kickImmediately: true })
}

function startBatchPolling(opts: { kickImmediately: boolean }): void {
  const state = batch.value
  if (!state) return

  state.timer = setInterval(() => {
    void batchPollTick()
  }, POLL_INTERVAL_MS)

  // Fresh fire: poll immediately so progress starts without waiting a full
  // interval. On mount re-attach the status was JUST read — no extra kick.
  if (opts.kickImmediately) void batchPollTick()
}

/**
 * One poll tick:
 *   - `done:false` → keep polling; emit 'refetch' when the recovered+failed
 *     counts advanced so recovered pages leave the list INCREMENTALLY via the
 *     parent's refreshed prop (REV-R30-S02). NEVER finalize early.
 *   - `done:true` → finalize with the REAL recovered/failed counts + final
 *     'refetch' (REV-R32: recovered rows land in Reconciliación,
 *     requires_review — the validation gate, never auto-accepted).
 *   - failsafe cap → finalize with last-known counts (hung-batch guard only).
 *   - network error → swallow and keep polling (transient); the cap bounds it.
 */
async function batchPollTick(): Promise<void> {
  const state = batch.value
  if (!state) return

  const elapsed = Date.now() - state.startedAt

  let status: DiscardedRecoverStatusResponse | null = null
  try {
    status = await getDiscardedRecoverStatus(props.runId)
  } catch {
    status = null // transient — keep polling until the cap.
  }

  // Re-read state: the component may have unmounted during the await.
  const current = batch.value
  if (!current) return

  if (status) {
    const progressed =
      status.recovered + status.failed >
      current.lastRecovered + current.lastFailed
    current.lastRecovered = status.recovered
    current.lastFailed = status.failed

    if (status.done) {
      finalizeBatch({ recovered: status.recovered, failed: status.failed })
      emit('refetch')
      return
    }

    // Incremental progress: ask the parent to refresh so completed pages
    // leave the discarded list now — not when the whole batch finishes.
    if (progressed) emit('refetch')
  }

  // Failsafe: never poll forever if the backend never reports done.
  if (elapsed >= current.capMs) {
    finalizeBatch({
      recovered: current.lastRecovered,
      failed: current.lastFailed,
    })
    emit('refetch')
  }
  // else: still running — keep polling.
}

function finalizeBatch(counts: { recovered: number; failed: number }): void {
  const state = batch.value
  if (!state) return
  if (state.timer) clearInterval(state.timer)
  batchSummary.value = {
    recovered: Math.max(0, counts.recovered),
    failed: Math.max(0, counts.failed),
  }
  batch.value = null
}

// ---------------------------------------------------------------------------
// PR-3b — A4 mount re-attach. The Descartadas tabpanel is v-if-mounted, so
// every tab visit polls recover-status ONCE; an in-flight batch (a 343-page
// run can take ~1 h) is re-attached instead of orphaned. The settled terminal
// shape {total:0, done:true} when no batch fired makes this safe on every
// mount (locked by backend contract test 2.1.15).
// ---------------------------------------------------------------------------

onMounted(() => {
  void reattachBatch()
})

async function reattachBatch(): Promise<void> {
  let status: DiscardedRecoverStatusResponse | null = null
  try {
    status = await getDiscardedRecoverStatus(props.runId)
  } catch {
    return // no signal — treat as no in-flight batch.
  }
  // Strict check: only an explicit done:false means an in-flight batch
  // (tolerates undefined/malformed responses from partial test mocks).
  if (!status || status.done !== false) return
  if (batch.value !== null) return

  batch.value = {
    total: status.total,
    startedAt: Date.now(),
    capMs: Math.max(1, status.total) * HARD_CAP_PER_PAGE_MS,
    timer: null,
    lastRecovered: status.recovered,
    lastFailed: status.failed,
  }
  startBatchPolling({ kickImmediately: false })
}

onBeforeUnmount(() => {
  const state = batch.value
  if (state?.timer) clearInterval(state.timer)
})
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

/* Batch progress / summary / error (PR-3b) */
.descartadas-tab__batch-progress {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  background-color: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  font-variant-numeric: tabular-nums;
}

.descartadas-tab__batch-summary {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--status-match-fg, #1a7f37);
  background-color: var(--status-match-bg, #e6f4ea);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  font-variant-numeric: tabular-nums;
}

.descartadas-tab__batch-error {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--status-mismatch-fg, #c0392b);
  background-color: var(--status-mismatch-bg, #fde8e8);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
}

/* Confirm dialog (Pendientes dialog pattern — A3) */
.descartadas-tab__dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 1000);
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgba(0, 0, 0, 0.45);
}

.descartadas-tab__dialog {
  width: min(440px, 90vw);
  padding: var(--space-5);
  background-color: var(--surface-overlay, var(--surface-raised));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg, 0 10px 30px rgba(0, 0, 0, 0.3));
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.descartadas-tab__dialog-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--text-primary);
}

.descartadas-tab__dialog-body,
.descartadas-tab__dialog-eta {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.descartadas-tab__dialog-warning {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--status-pending-fg, #92400e);
  background-color: var(--status-pending-bg, #fef3c7);
  border-radius: var(--radius-md);
}

.descartadas-tab__dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.descartadas-tab__dialog-cancel,
.descartadas-tab__dialog-confirm {
  padding: var(--space-1) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 600;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.descartadas-tab__dialog-cancel {
  border: 1px solid var(--border-default);
  background-color: var(--surface-raised);
  color: var(--text-primary);
}

.descartadas-tab__dialog-cancel:hover {
  background-color: var(--surface-hover);
}

.descartadas-tab__dialog-confirm {
  border: 1px solid var(--color-primary, #4f46e5);
  background-color: var(--color-primary, #4f46e5);
  color: #fff;
}

.descartadas-tab__dialog-confirm:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}
</style>
