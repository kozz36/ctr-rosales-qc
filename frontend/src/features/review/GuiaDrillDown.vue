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
            <!-- Guía ID -->
            <td class="guia-drill-down__td">
              <span class="guia-drill-down__guia-id mono">{{ guia.guia_id }}</span>
            </td>

            <!-- Source pages (comma-separated) -->
            <td class="guia-drill-down__td">
              <span class="guia-drill-down__pages mono">{{ guia.source_pages.join(', ') }}</span>
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

            <!-- Actions -->
            <td class="guia-drill-down__td guia-drill-down__td--actions">
              <button
                class="guia-drill-down__reassign-btn"
                :aria-label="`Reasignar guía ${guia.guia_id}`"
                title="Reasignar guía"
                @click="emit('reassign', guia.guia_id)"
              >
                <span aria-hidden="true">⇄</span>
                Reasignar
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </td>
  </tr>
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

import { ref, nextTick } from 'vue'
import type { GuiaContributionResponse } from '@/api/types'
import { useGuiaLineEdit } from '@/composables/useReconciliationApi'
import ConfidenceBadge from './ConfidenceBadge.vue'
import YearInferredBadge from './YearInferredBadge.vue'
import FechaDivergenceBadge from './FechaDivergenceBadge.vue'

// The row spans all aggregate columns (10 data + 1 expand + 1 actions = 12; rev-3 +1 for Fecha = 13).
// Using a high number is safe — browsers clip at the actual column count.
const COLSPAN = 13

const props = defineProps<{
  /** Contributing guías from the already-fetched ReconciliationRowResponse.guias[] */
  guias: GuiaContributionResponse[]
  /** Run ID needed for the PATCH /guias/{id}/lines mutation */
  runId: string
}>()

const emit = defineEmits<{
  /** Emitted when the user clicks "Reassign" on a guía row. */
  reassign: [guiaId: string]
  /** Emitted after a successful cantidad edit so the parent can react. */
  rowUpdated: []
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
    { guiaId: guia.guia_id, body: { line_index: null, material_canonical: null, cantidad: parsed } },
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

.guia-drill-down__guia-id {
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

/* Reassign button */
.guia-drill-down__reassign-btn {
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

.guia-drill-down__reassign-btn:hover {
  border-color: var(--action-primary);
  color: var(--action-primary-hover);
  background-color: rgba(31, 111, 235, 0.08);
}

.guia-drill-down__reassign-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}
</style>
