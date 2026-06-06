<template>
  <tr class="guia-drill-down" aria-label="Detalle de guías de remisión">
    <td :colspan="COLSPAN" class="guia-drill-down__cell">
      <table class="guia-drill-down__table" aria-label="Guías de remisión contribuyentes">
        <thead class="guia-drill-down__thead">
          <tr>
            <th class="guia-drill-down__th" scope="col">Guía (serie-número)</th>
            <th class="guia-drill-down__th" scope="col">Páginas</th>
            <th class="guia-drill-down__th guia-drill-down__th--numeric" scope="col">Cantidad</th>
            <th class="guia-drill-down__th" scope="col">Unidad</th>
            <th class="guia-drill-down__th" scope="col">Confianza</th>
            <th class="guia-drill-down__th" scope="col">Identidad</th>
            <th class="guia-drill-down__th" scope="col">Fecha</th>
            <th class="guia-drill-down__th" scope="col" aria-label="Acciones" />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="guia in guias"
            :key="guia.guia_id"
            class="guia-drill-down__row"
            :class="{ 'guia-drill-down__row--divergent': guia.fecha_divergence }"
            :data-identity="guia.identity_source"
          >
            <!-- Guía ID — clickable affordance opens the page viewer (F2 refinement
                 #3012): clicking the serie-número opens PageSheetViewer at the guía's
                 first source page. Native <button> = keyboard-accessible (Enter/Space). -->
            <td class="guia-drill-down__td">
              <button
                v-if="guia.source_pages.length > 0"
                type="button"
                class="guia-drill-down__guia-id mono"
                :aria-label="`Ver página ${guia.source_pages[0]} de la guía ${guia.guia_id}`"
                :title="`Ver guía ${guia.guia_id} (pág. ${guia.source_pages[0]})`"
                @click="onGuiaIdClick(guia)"
              >
                {{ guia.guia_id }}
              </button>
              <span v-else class="guia-drill-down__guia-id mono">{{ guia.guia_id }}</span>
            </td>

            <!-- Source pages — interactive chips open the page viewer (F2 / REV-R22). -->
            <td class="guia-drill-down__td">
              <SourcePages
                :pages="guia.source_pages"
                :run-id="runId"
                @page-click="emit('pageClick', $event)"
              />
            </td>

            <!-- Cantidad (editable) -->
            <td class="guia-drill-down__td guia-drill-down__td--numeric">
              <div v-if="editingGuiaId === guia.guia_id" class="guia-drill-down__edit-wrap">
                <input
                  :ref="(el) => setInputRef(guia.guia_id, el as HTMLInputElement | null)"
                  v-model="editValue"
                  class="guia-drill-down__input mono"
                  type="text"
                  inputmode="decimal"
                  :aria-label="`Editar cantidad de guía ${guia.guia_id}`"
                  @blur="commitEdit(guia)"
                  @keydown.enter="commitEdit(guia)"
                  @keydown.escape="cancelEdit"
                />
              </div>
              <button
                v-else
                class="guia-drill-down__editable-cell"
                :aria-label="`Cantidad ${formatDecimal(guia.cantidad)} — clic para editar`"
                :aria-busy="isPendingFor(guia.guia_id)"
                @click="startEdit(guia)"
              >
                <span class="mono">{{ formatDecimal(guia.cantidad) }}</span>
                <span v-if="isPendingFor(guia.guia_id)" class="guia-drill-down__spinner" aria-hidden="true" />
                <span v-else class="guia-drill-down__edit-hint" aria-hidden="true">✎</span>
              </button>
            </td>

            <!-- Unidad -->
            <td class="guia-drill-down__td">
              <span class="mono">{{ guia.unidad }}</span>
            </td>

            <!-- Confidence badge -->
            <td class="guia-drill-down__td">
              <ConfidenceBadge :value="guia.confidence" compact />
            </td>

            <!-- Identity source indicator -->
            <td class="guia-drill-down__td">
              <span
                v-if="guia.identity_source === 'qr'"
                class="guia-drill-down__identity-badge guia-drill-down__identity-badge--qr"
                aria-label="Identidad por código QR"
              >
                QR
              </span>
              <span
                v-else
                class="guia-drill-down__identity-badge guia-drill-down__identity-badge--ocr"
                aria-label="Identidad por OCR (fallback)"
              >
                OCR fallback
              </span>
            </td>

            <!-- Fecha advisory (year_inferred) + divergence (red) -->
            <td class="guia-drill-down__td">
              <FechaDivergenceBadge
                v-if="guia.fecha_divergence"
              />
              <YearInferredBadge
                v-if="guia.year_inferred"
                compact
              />
              <span
                v-if="!guia.fecha_divergence && !guia.year_inferred"
                class="guia-drill-down__fecha-ok"
                aria-hidden="true"
              >—</span>
            </td>

            <!-- Actions — [Acciones] disclosure menu (F4 / REV-R24, D8) -->
            <td class="guia-drill-down__td guia-drill-down__td--actions">
              <div class="guia-drill-down__acciones">
                <button
                  :ref="(el) => setTriggerRef(guia.guia_id, el as HTMLButtonElement | null)"
                  type="button"
                  class="guia-drill-down__acciones-trigger"
                  :aria-label="`Acciones para la guía ${guia.guia_id}`"
                  aria-haspopup="menu"
                  :aria-expanded="openMenuId === guia.guia_id"
                  @click.stop="toggleMenu(guia.guia_id)"
                  @keydown.down.prevent="openMenu(guia.guia_id)"
                >
                  Acciones
                  <span class="guia-drill-down__acciones-caret" aria-hidden="true">▾</span>
                </button>

                <ul
                  v-if="openMenuId === guia.guia_id"
                  class="guia-drill-down__menu"
                  role="menu"
                  :aria-label="`Acciones para la guía ${guia.guia_id}`"
                  @keydown="onMenuKeydown($event, guia)"
                >
                  <li role="none">
                    <button
                      :ref="(el) => registerMenuItem(el as HTMLButtonElement | null, 0)"
                      type="button"
                      role="menuitem"
                      class="guia-drill-down__menu-item"
                      @click="onReassign(guia)"
                    >
                      <span aria-hidden="true">⇄</span> Reasignar
                    </button>
                  </li>
                  <li role="none">
                    <button
                      :ref="(el) => registerMenuItem(el as HTMLButtonElement | null, 1)"
                      type="button"
                      role="menuitem"
                      class="guia-drill-down__menu-item"
                      :disabled="reprocessingIds.has(guia.guia_id) || undefined"
                      :aria-busy="reprocessingIds.has(guia.guia_id)"
                      @click="onReprocess(guia)"
                    >
                      <span aria-hidden="true">↻</span>
                      {{ reprocessingIds.has(guia.guia_id) ? 'Reprocesando…' : 'Reprocesar' }}
                    </button>
                  </li>
                  <li role="none">
                    <button
                      :ref="(el) => registerMenuItem(el as HTMLButtonElement | null, 2)"
                      type="button"
                      role="menuitem"
                      class="guia-drill-down__menu-item"
                      @click="onCorregir(guia)"
                    >
                      <span aria-hidden="true">✎</span> Corregir manual
                    </button>
                  </li>
                </ul>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </td>
  </tr>

  <!-- Corregir manual dialog (F4 / REV-R25, D9): operator assigns a declared material
       of THIS registro + cantidad. The corrected line carries match_method="operator"
       + requires_review=True (set by the backend). -->
  <Teleport to="body">
    <div
      v-if="correctTarget"
      class="dialog-backdrop"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="correctTitleId"
      @click.self="closeCorrect"
      @keydown.escape="closeCorrect"
    >
      <div ref="correctDialogRef" class="dialog guia-correct" tabindex="-1" @keydown="onCorrectDialogKeydown">
        <div class="dialog__header">
          <h2 :id="correctTitleId" class="dialog__title">Corregir manual</h2>
          <button class="dialog__close" aria-label="Cerrar diálogo" @click="closeCorrect">
            <span aria-hidden="true">✕</span>
          </button>
        </div>

        <div class="dialog__context">
          <div class="dialog__context-row">
            <span class="dialog__context-label">Guía</span>
            <span class="dialog__context-value mono dialog__context-value--highlight">{{ correctTarget.guia_id }}</span>
          </div>
          <div class="dialog__context-row">
            <span class="dialog__context-label">Registro</span>
            <span class="dialog__context-value mono">{{ registro }}</span>
          </div>
        </div>

        <div class="dialog__warning" role="note">
          <span class="dialog__warning-icon" aria-hidden="true">⚠</span>
          <p>
            Asignación manual del operador — la línea quedará marcada como
            <strong>requiere revisión</strong> y no se acepta automáticamente.
          </p>
        </div>

        <form class="dialog__form" @submit.prevent="submitCorrect" novalidate>
          <div class="dialog__field">
            <label :for="materialSelectId" class="dialog__label">
              Material declarado <span class="dialog__required" aria-hidden="true">*</span>
            </label>
            <select
              :id="materialSelectId"
              ref="materialSelectRef"
              v-model="correctForm.materialKey"
              class="dialog__input guia-correct__material-select"
            >
              <option value="" disabled>— Seleccionar material —</option>
              <option
                v-for="opt in declaredOptions"
                :key="opt.key"
                class="guia-correct__material-option"
                :value="opt.key"
              >
                {{ opt.material_canonical }} — {{ opt.unidad }}
              </option>
            </select>
          </div>

          <div class="dialog__field">
            <label :for="cantidadInputId" class="dialog__label">
              Cantidad <span class="dialog__required" aria-hidden="true">*</span>
            </label>
            <input
              :id="cantidadInputId"
              v-model="correctForm.cantidad"
              class="dialog__input mono guia-correct__cantidad-input"
              type="text"
              inputmode="decimal"
              placeholder="ej: 500"
              autocomplete="off"
            />
          </div>

          <div class="dialog__footer">
            <button type="button" class="dialog__btn dialog__btn--secondary" @click="closeCorrect">
              Cancelar
            </button>
            <button
              type="submit"
              class="dialog__btn dialog__btn--primary guia-correct__submit"
              :disabled="!isCorrectValid"
            >
              Guardar corrección
            </button>
          </div>
        </form>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * GuiaDrillDown — inline sub-table showing the contributing guías for one
 * reconciliation row.
 *
 * Design contracts:
 * - Props receive `guias[]` already fetched as part of ReconciliationRowResponse
 *   (NO additional API call on expand — REV-C01).
 * - Editable `cantidad` cell triggers useGuiaLineEdit on commit (REV-C03).
 * - "Reassign" button emits `reassign(guia_id)` — parent opens GuiaReassignDialog
 *   with the correct guia_id (REV-C02).
 */

import { ref, computed, nextTick } from 'vue'
import type { GuiaContributionResponse, ReconciliationRowResponse } from '@/api/types'
import { useGuiaLineEdit } from '@/composables/useReconciliationApi'
import { reprocessGuia } from '@/api/client'
import ConfidenceBadge from './ConfidenceBadge.vue'
import YearInferredBadge from './YearInferredBadge.vue'
import FechaDivergenceBadge from './FechaDivergenceBadge.vue'
import SourcePages from './SourcePages.vue'

// The drill-down row spans the full parent table: 1 expand + 10 data columns = 11.
// This MUST match the exact column count — an OVER-count colspan does NOT clip; under
// table-layout:fixed it creates phantom auto columns that steal slack from the auto
// Material column (#23 regression). Keep in sync with ReviewGrid COLUMNS + expand.
const COLSPAN = 11

const props = defineProps<{
  /** Contributing guías from the already-fetched ReconciliationRowResponse.guias[] */
  guias: GuiaContributionResponse[]
  /** Run ID needed for the PATCH /guias/{id}/lines mutation */
  runId: string
  /**
   * Canonical material key of the parent reconciliation row (B1). Sent as the
   * line selector in the line-edit mutation so the backend can locate the line;
   * without it the backend rejected every inline cantidad edit with HTTP 422.
   */
  materialCanonical: string
  /**
   * F4 / REV-R25 (D9): the registro this drill-down's guías belong to. Used to
   * scope the "Corregir manual" declared-material dropdown to THIS registro only.
   */
  registro: string
  /**
   * F4 / REV-R25 (D9): the full reconciliation table rows (already in the table
   * response). The "Corregir manual" dropdown is sourced from
   * tableRows.filter(r => r.registro === props.registro) — no extra API call.
   */
  tableRows?: ReconciliationRowResponse[]
}>()

const emit = defineEmits<{
  /** Emitted when the user clicks "Reassign" on a guía row. */
  reassign: [guiaId: string]
  /** Emitted after a successful cantidad edit so the parent can react. */
  rowUpdated: []
  /** F2 / REV-R22: a page chip or the serie-número was clicked → open viewer. */
  pageClick: [page: number]
}>()

// ---------------------------------------------------------------------------
// Inline edit state
// ---------------------------------------------------------------------------

const editingGuiaId = ref<string | null>(null)
const editValue = ref('')
const inputRefs = new Map<string, HTMLInputElement>()

const runIdRef = ref(props.runId)
const { mutate, isPending: isMutationPending, variables } = useGuiaLineEdit(runIdRef)

function isPendingFor(guiaId: string): boolean {
  return isMutationPending.value && variables.value?.guiaId === guiaId
}

function setInputRef(guiaId: string, el: HTMLInputElement | null): void {
  if (el) {
    inputRefs.set(guiaId, el)
  } else {
    inputRefs.delete(guiaId)
  }
}

function startEdit(guia: GuiaContributionResponse): void {
  editValue.value = guia.cantidad
  editingGuiaId.value = guia.guia_id
  void nextTick(() => inputRefs.get(guia.guia_id)?.select())
}

function commitEdit(guia: GuiaContributionResponse): void {
  if (editingGuiaId.value !== guia.guia_id) return
  const raw = editValue.value.trim()
  const parsed = Number(raw)
  if (!raw || isNaN(parsed) || parsed < 0) {
    cancelEdit()
    return
  }
  if (raw === guia.cantidad) {
    cancelEdit()
    return
  }
  editingGuiaId.value = null
  mutate(
    {
      guiaId: guia.guia_id,
      // B1: send the parent row's canonical material as the line selector. The
      // backend matches by description_canonical and requires a non-null selector,
      // otherwise it raises ValueError → 422 (dead inline-edit feature).
      body: { line_index: null, material_canonical: props.materialCanonical, cantidad: parsed },
    },
    {
      onSuccess: () => emit('rowUpdated'),
    },
  )
}

function cancelEdit(): void {
  editingGuiaId.value = null
  editValue.value = ''
}

// ---------------------------------------------------------------------------
// F2 / REV-R22 — serie-número click opens the page viewer at the guía's first page
// ---------------------------------------------------------------------------

function onGuiaIdClick(guia: GuiaContributionResponse): void {
  const first = guia.source_pages[0]
  if (first !== undefined) emit('pageClick', first)
}

// ---------------------------------------------------------------------------
// F4 / REV-R24 — [Acciones] disclosure menu (one menu per guía row)
// ---------------------------------------------------------------------------

const openMenuId = ref<string | null>(null)
const triggerRefs = new Map<string, HTMLButtonElement>()
const menuItemRefs = ref<(HTMLButtonElement | null)[]>([])

function setTriggerRef(guiaId: string, el: HTMLButtonElement | null): void {
  if (el) triggerRefs.set(guiaId, el)
  else triggerRefs.delete(guiaId)
}

function registerMenuItem(el: HTMLButtonElement | null, index: number): void {
  menuItemRefs.value[index] = el
}

function openMenu(guiaId: string): void {
  openMenuId.value = guiaId
  menuItemRefs.value = []
  void nextTick(() => menuItemRefs.value[0]?.focus())
}

function closeMenu(restoreFocus = false): void {
  const id = openMenuId.value
  openMenuId.value = null
  if (restoreFocus && id) {
    void nextTick(() => triggerRefs.get(id)?.focus())
  }
}

function toggleMenu(guiaId: string): void {
  if (openMenuId.value === guiaId) closeMenu(true)
  else openMenu(guiaId)
}

/** Roving focus + Escape inside the open menu (WAI-ARIA menu pattern). */
function onMenuKeydown(event: KeyboardEvent, _guia: GuiaContributionResponse): void {
  const items = menuItemRefs.value.filter((el): el is HTMLButtonElement => !!el)
  if (items.length === 0) return
  const active = document.activeElement as HTMLElement | null
  const idx = items.findIndex((el) => el === active)
  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault()
      items[(idx + 1) % items.length]?.focus()
      break
    case 'ArrowUp':
      event.preventDefault()
      items[(idx - 1 + items.length) % items.length]?.focus()
      break
    case 'Home':
      event.preventDefault()
      items[0]?.focus()
      break
    case 'End':
      event.preventDefault()
      items[items.length - 1]?.focus()
      break
    case 'Escape':
      event.preventDefault()
      closeMenu(true)
      break
    case 'Tab':
      // Closing on Tab keeps the menu modal-light (focus leaves the menu).
      closeMenu(false)
      break
    default:
      break
  }
}

// --- Reasignar (existing flow) ---
function onReassign(guia: GuiaContributionResponse): void {
  closeMenu(false)
  emit('reassign', guia.guia_id)
}

// --- Reprocesar (reuse the existing per-guía reprocess endpoint, PR#3) ---
const reprocessingIds = ref<Set<string>>(new Set())

async function onReprocess(guia: GuiaContributionResponse): Promise<void> {
  if (reprocessingIds.value.has(guia.guia_id)) return
  reprocessingIds.value = new Set([...reprocessingIds.value, guia.guia_id])
  closeMenu(false)
  try {
    await reprocessGuia(props.runId, guia.guia_id)
    // The table cache is invalidated by the parent on rowUpdated so the new
    // backend state (recovered line / requires_review) becomes visible.
    emit('rowUpdated')
  } finally {
    const next = new Set(reprocessingIds.value)
    next.delete(guia.guia_id)
    reprocessingIds.value = next
  }
}

// ---------------------------------------------------------------------------
// F4 / REV-R25 — "Corregir manual" dialog (operator-assigned canonical)
// ---------------------------------------------------------------------------

const uid = Math.random().toString(36).slice(2, 8)
const correctTitleId = `guia-correct-title-${uid}`
const materialSelectId = `guia-correct-material-${uid}`
const cantidadInputId = `guia-correct-cantidad-${uid}`

const correctTarget = ref<GuiaContributionResponse | null>(null)
const correctForm = ref<{ materialKey: string; cantidad: string }>({ materialKey: '', cantidad: '' })
const correctDialogRef = ref<HTMLDivElement | null>(null)
const materialSelectRef = ref<HTMLSelectElement | null>(null)

interface DeclaredOption {
  key: string // "{material_canonical}|{unidad}"
  material_canonical: string
  unidad: string
}

/**
 * Declared materials for THIS registro only (REV-R25-S01). Sourced from the table
 * response rows the parent already has — no extra API call. De-duplicated by
 * (material_canonical, unidad).
 */
const declaredOptions = computed<DeclaredOption[]>(() => {
  const rows = props.tableRows ?? []
  const seen = new Map<string, DeclaredOption>()
  for (const r of rows) {
    if (r.registro !== props.registro) continue
    const key = `${r.material_canonical}|${r.unidad}`
    if (!seen.has(key)) {
      seen.set(key, { key, material_canonical: r.material_canonical, unidad: r.unidad })
    }
  }
  return Array.from(seen.values())
})

const isCorrectValid = computed(() => {
  if (!correctForm.value.materialKey) return false
  const n = Number(correctForm.value.cantidad.trim())
  return correctForm.value.cantidad.trim() !== '' && !isNaN(n) && n >= 0
})

function onCorregir(guia: GuiaContributionResponse): void {
  closeMenu(false)
  correctTarget.value = guia
  correctForm.value = { materialKey: '', cantidad: guia.cantidad }
  void nextTick(() => (materialSelectRef.value ?? correctDialogRef.value)?.focus())
}

function closeCorrect(): void {
  const id = correctTarget.value?.guia_id
  correctTarget.value = null
  if (id) void nextTick(() => triggerRefs.get(id)?.focus())
}

/** Focus-trap inside the dialog (Tab/Shift+Tab cycle). */
function onCorrectDialogKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Tab') return
  const root = correctDialogRef.value
  if (!root) return
  const focusables = Array.from(
    root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => !el.hasAttribute('disabled'))
  if (focusables.length === 0) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const active = document.activeElement as HTMLElement | null
  if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

function submitCorrect(): void {
  const guia = correctTarget.value
  if (!guia || !isCorrectValid.value) return
  const opt = declaredOptions.value.find((o) => o.key === correctForm.value.materialKey)
  if (!opt) return
  const parsed = Number(correctForm.value.cantidad.trim())
  correctTarget.value = null
  mutate(
    {
      guiaId: guia.guia_id,
      // D9: assign_material_canonical drives the backend operator-assigned path
      // (match_method="operator" + requires_review=True). material_canonical is
      // still sent as the line selector so the backend can locate the line.
      body: {
        line_index: null,
        material_canonical: props.materialCanonical,
        assign_material_canonical: opt.material_canonical,
        cantidad: parsed,
      },
    },
    {
      onSuccess: () => emit('rowUpdated'),
    },
  )
}

// ---------------------------------------------------------------------------
// Formatting helpers (mirrors ReconciliationRow)
// ---------------------------------------------------------------------------

function formatDecimal(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (isNaN(n)) return value
  return n.toLocaleString('es-PE', { maximumFractionDigits: 4, minimumFractionDigits: 0 })
}

// Expose for tests
defineExpose({ editingGuiaId, editValue })
</script>

<style scoped>
.guia-drill-down {
  background-color: var(--surface-inset);
}

.guia-drill-down__cell {
  padding: var(--space-3) var(--space-4) var(--space-3) var(--space-6);
  border-bottom: 1px solid var(--border-subtle);
}

/* Sub-table */
.guia-drill-down__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
}

.guia-drill-down__thead {
  border-bottom: 1px solid var(--border-subtle);
}

.guia-drill-down__th {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-2xs);
  font-weight: 500;
  color: var(--text-tertiary);
  letter-spacing: 0.05em;
  text-transform: uppercase;
  white-space: nowrap;
  text-align: left;
}

.guia-drill-down__th--numeric {
  text-align: right;
}

/* Data rows */
.guia-drill-down__row {
  border-bottom: 1px solid var(--border-subtle);
  transition: background-color var(--transition-fast);
}

.guia-drill-down__row:last-child {
  border-bottom: none;
}

.guia-drill-down__row:hover {
  background-color: var(--surface-hover);
}

/* R9 (FDR-009): RED highlight for a guía with a diverging reception date. */
.guia-drill-down__row--divergent {
  border-left: 3px solid var(--status-mismatch-fg);
  background-color: var(--status-mismatch-bg);
}

.guia-drill-down__row--divergent:hover {
  background-color: var(--status-mismatch-bg);
}

.guia-drill-down__td {
  padding: var(--space-2) var(--space-3);
  color: var(--text-primary);
  vertical-align: middle;
  white-space: nowrap;
}

.guia-drill-down__td--numeric {
  text-align: right;
}

.guia-drill-down__td--actions {
  width: 100px;
}

/* Guía serie-número — clickable affordance opens the page viewer (F2). */
button.guia-drill-down__guia-id {
  font-family: var(--font-mono);
  color: var(--action-primary, var(--text-secondary));
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 2px var(--space-2);
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 2px;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast),
    color var(--transition-fast);
}

button.guia-drill-down__guia-id:hover {
  border-color: var(--border-default);
  background-color: var(--surface-inset);
  color: var(--action-primary-hover, var(--text-primary));
}

button.guia-drill-down__guia-id:focus-visible {
  outline: none;
  border-color: var(--border-focus);
  box-shadow: var(--shadow-focus);
}

span.guia-drill-down__guia-id {
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

.guia-drill-down__pages {
  font-family: var(--font-mono);
  color: var(--text-tertiary);
}

/* Editable cantidad cell */
.guia-drill-down__editable-cell {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 2px var(--space-2);
  cursor: pointer;
  color: inherit;
  font-size: inherit;
  min-width: 60px;
  justify-content: flex-end;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast);
}

.guia-drill-down__editable-cell:hover {
  border-color: var(--border-default);
  background-color: var(--surface-inset);
}

.guia-drill-down__editable-cell:focus-visible {
  outline: none;
  border-color: var(--border-focus);
  box-shadow: var(--shadow-focus);
}

.guia-drill-down__edit-hint {
  color: var(--text-tertiary);
  font-size: var(--text-2xs);
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.guia-drill-down__editable-cell:hover .guia-drill-down__edit-hint {
  opacity: 1;
}

.guia-drill-down__edit-wrap {
  display: flex;
  justify-content: flex-end;
}

.guia-drill-down__input {
  width: 80px;
  padding: 2px var(--space-2);
  background-color: var(--surface-inset);
  border: 1px solid var(--border-focus);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: var(--text-sm);
  text-align: right;
  box-shadow: var(--shadow-focus);
}

.guia-drill-down__input:focus {
  outline: none;
}

.guia-drill-down__spinner {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(255, 255, 255, 0.2);
  border-top-color: var(--action-primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Identity badges */
.guia-drill-down__identity-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px var(--space-2);
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  white-space: nowrap;
}

.guia-drill-down__identity-badge--qr {
  color: var(--confidence-ok);
  background-color: rgba(63, 185, 80, 0.12);
  border: 1px solid rgba(63, 185, 80, 0.25);
}

.guia-drill-down__identity-badge--ocr {
  color: var(--confidence-low);
  background-color: rgba(227, 179, 65, 0.12);
  border: 1px solid rgba(227, 179, 65, 0.25);
  text-transform: none;
  letter-spacing: 0;
  font-weight: 400;
  font-size: var(--text-xs);
}

/* Year-inferred OK placeholder */
.guia-drill-down__fecha-ok {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

/* Acciones disclosure menu (F4 / REV-R24) */
.guia-drill-down__td--actions {
  width: 120px;
}

.guia-drill-down__acciones {
  position: relative;
  display: inline-block;
}

.guia-drill-down__acciones-trigger {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-default);
  background-color: transparent;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  cursor: pointer;
  white-space: nowrap;
  transition:
    border-color var(--transition-fast),
    color var(--transition-fast),
    background-color var(--transition-fast);
}

.guia-drill-down__acciones-trigger:hover,
.guia-drill-down__acciones-trigger[aria-expanded='true'] {
  border-color: var(--action-primary);
  color: var(--action-primary-hover);
  background-color: rgba(31, 111, 235, 0.08);
}

.guia-drill-down__acciones-trigger:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.guia-drill-down__acciones-caret {
  font-size: var(--text-2xs);
  opacity: 0.7;
}

.guia-drill-down__menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  z-index: 20;
  min-width: 168px;
  margin: 0;
  padding: var(--space-1);
  list-style: none;
  background-color: var(--surface-overlay);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
}

.guia-drill-down__menu-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-primary);
  font-size: var(--text-xs);
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.guia-drill-down__menu-item:hover {
  background-color: var(--surface-hover);
}

.guia-drill-down__menu-item:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.guia-drill-down__menu-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Corregir-manual dialog (F4 / REV-R25) — mirrors GuiaReassignDialog tokens. */
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(2px);
  padding: var(--space-4);
  animation: fade-in var(--transition-normal) ease;
}

@keyframes fade-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}

.dialog {
  width: 100%;
  max-width: 460px;
  background-color: var(--surface-overlay);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.dialog:focus {
  outline: none;
}

.dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-5) var(--space-6);
  border-bottom: 1px solid var(--border-subtle);
}

.dialog__title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--text-primary);
}

.dialog__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  border: none;
  background: none;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.dialog__close:hover {
  background-color: var(--surface-hover);
  color: var(--text-primary);
}

.dialog__close:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.dialog__context {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-4) var(--space-6);
  background-color: var(--surface-inset);
  border-bottom: 1px solid var(--border-subtle);
}

.dialog__context-row {
  display: contents;
}

.dialog__context-label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  white-space: nowrap;
}

.dialog__context-value {
  font-size: var(--text-xs);
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.dialog__context-value--highlight {
  font-weight: 600;
  color: var(--confidence-ok);
}

.dialog__warning {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  padding: var(--space-3) var(--space-6);
  background-color: rgba(227, 179, 65, 0.08);
  border-bottom: 1px solid rgba(227, 179, 65, 0.2);
}

.dialog__warning-icon {
  flex-shrink: 0;
  color: var(--confidence-low);
  font-size: var(--text-sm);
  margin-top: 1px;
}

.dialog__warning p {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  line-height: 1.6;
}

.dialog__form {
  padding: var(--space-5) var(--space-6) var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.dialog__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.dialog__label {
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--text-secondary);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.dialog__required {
  color: var(--status-mismatch-fg);
}

.dialog__input {
  padding: var(--space-2) var(--space-3);
  background-color: var(--surface-inset);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: var(--text-sm);
  transition: border-color var(--transition-fast);
}

.dialog__input:focus {
  outline: none;
  border-color: var(--border-focus);
  box-shadow: var(--shadow-focus);
}

.dialog__footer {
  display: flex;
  gap: var(--space-3);
  justify-content: flex-end;
  margin-top: var(--space-2);
}

.dialog__btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    opacity var(--transition-fast);
}

.dialog__btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.dialog__btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.dialog__btn--secondary {
  background-color: transparent;
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
}

.dialog__btn--secondary:not(:disabled):hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}

.dialog__btn--primary {
  background-color: var(--action-primary);
  border: 1px solid var(--action-primary);
  color: var(--text-primary);
}

.dialog__btn--primary:not(:disabled):hover {
  background-color: var(--action-primary-hover);
  border-color: var(--action-primary-hover);
}
</style>
