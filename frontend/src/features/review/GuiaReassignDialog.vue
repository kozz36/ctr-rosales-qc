<template>
  <Teleport to="body">
    <div
      v-if="modelValue"
      class="dialog-backdrop"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="dialogTitleId"
      :aria-describedby="dialogDescId"
      @click.self="onBackdropClick"
      @keydown.escape="close"
    >
      <div class="dialog" ref="dialogRef" tabindex="-1">
        <!-- Header -->
        <div class="dialog__header">
          <h2 :id="dialogTitleId" class="dialog__title">Reasignar guía</h2>
          <button
            class="dialog__close"
            aria-label="Cerrar diálogo"
            @click="close"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>

        <!-- Context: guía + current row -->
        <div class="dialog__context" :id="dialogDescId">
          <div class="dialog__context-row">
            <span class="dialog__context-label">Guía</span>
            <span class="dialog__context-value mono dialog__context-value--highlight">{{ guiaId }}</span>
          </div>
          <div class="dialog__context-row">
            <span class="dialog__context-label">Registro actual</span>
            <span class="dialog__context-value mono">{{ row?.registro ?? '—' }}</span>
          </div>
          <div class="dialog__context-row">
            <span class="dialog__context-label">Fecha actual</span>
            <span class="dialog__context-value mono">{{ row?.fecha ?? '—' }}</span>
          </div>
          <div class="dialog__context-row">
            <span class="dialog__context-label">Material</span>
            <span class="dialog__context-value">{{ row?.material_canonical ?? '—' }}</span>
          </div>
        </div>

        <!-- Warning: fecha edit re-groups -->
        <div class="dialog__warning" role="note" aria-label="Advertencia de reagrupación">
          <span class="dialog__warning-icon" aria-hidden="true">⚠</span>
          <p>
            Cambiar la <strong>fecha</strong> mueve la guía a un grupo diferente —
            la fila actual desaparecerá de este grupo y se recomputará el delta.
            Esta acción se registra en el historial de auditoría.
          </p>
        </div>

        <!-- Form -->
        <form class="dialog__form" @submit.prevent="onSubmit" novalidate>
          <div class="dialog__field">
            <label :for="registroInputId" class="dialog__label">
              Nuevo Registro <span class="dialog__required" aria-hidden="true">*</span>
            </label>
            <input
              :id="registroInputId"
              v-model="form.registro"
              class="dialog__input mono"
              type="text"
              :aria-required="true"
              :aria-invalid="!!errors.registro"
              :aria-describedby="errors.registro ? registroErrorId : undefined"
              placeholder="ej: 4251"
              autocomplete="off"
              spellcheck="false"
            />
            <p v-if="errors.registro" :id="registroErrorId" class="dialog__error" role="alert">
              {{ errors.registro }}
            </p>
          </div>

          <div class="dialog__field">
            <label :for="fechaInputId" class="dialog__label">
              Nueva Fecha
              <span class="dialog__hint">(YYYY-MM-DD — vacío para mantener)</span>
            </label>
            <input
              :id="fechaInputId"
              v-model="form.fecha"
              class="dialog__input mono"
              type="text"
              :aria-invalid="!!errors.fecha"
              :aria-describedby="errors.fecha ? fechaErrorId : undefined"
              placeholder="ej: 2024-03-15"
              autocomplete="off"
              spellcheck="false"
            />
            <p v-if="errors.fecha" :id="fechaErrorId" class="dialog__error" role="alert">
              {{ errors.fecha }}
            </p>
          </div>

          <!-- Footer buttons -->
          <div class="dialog__footer">
            <button
              type="button"
              class="dialog__btn dialog__btn--secondary"
              @click="close"
              :disabled="isPending"
            >
              Cancelar
            </button>
            <button
              type="submit"
              class="dialog__btn dialog__btn--primary"
              :disabled="isPending || !isFormValid"
              :aria-busy="isPending"
            >
              <span v-if="isPending" class="dialog__btn-spinner" aria-hidden="true" />
              {{ isPending ? 'Reasignando...' : 'Confirmar reasignación' }}
            </button>
          </div>
        </form>

        <!-- API Error -->
        <div v-if="apiError" class="dialog__api-error" role="alert">
          <span aria-hidden="true">✕</span>
          {{ apiError }}
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * GuiaReassignDialog — modal for reassigning a guía to a different registro/fecha.
 *
 * Flow:
 *   1. User opens dialog (parent sets modelValue=true + passes row)
 *   2. User fills new_registro (required) + new_fecha (optional)
 *   3. On submit: POST /runs/{id}/reassign → parent refetches table
 *   4. Dialog closes on success or on explicit cancel/Escape
 *
 * Spec: REV-003, REC-006. Warning per LOW-7: editing fecha re-groups the row.
 * A11y: focus trap on open, Escape closes, aria-modal=true.
 */

import { ref, computed, watch, nextTick } from 'vue'
import type { ReconciliationRowResponse } from '@/api/types'

// Unique ID prefix for this instance
const uid = Math.random().toString(36).slice(2, 8)
const dialogTitleId = `dialog-title-${uid}`
const dialogDescId = `dialog-desc-${uid}`
const registroInputId = `dialog-registro-${uid}`
const registroErrorId = `dialog-registro-err-${uid}`
const fechaInputId = `dialog-fecha-${uid}`
const fechaErrorId = `dialog-fecha-err-${uid}`

const props = defineProps<{
  /** Controls visibility */
  modelValue: boolean
  /**
   * The guía to reassign — identified by its serie-numero string (e.g. "T009-0741770").
   * This is the authoritative identifier sent to POST /reassign (REV-C02 / CRITICAL-1 fix).
   */
  guiaId: string
  /** The row that currently owns this guía (used for context display only). */
  row: ReconciliationRowResponse | null
  /** Is the mutation in-flight? */
  isPending?: boolean
  /** API error from mutation (null when none) */
  apiError?: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /**
   * Submit payload — guia_id is always props.guiaId (not row.row_id).
   * Parent calls POST /runs/{id}/reassign with this body.
   */
  submit: [payload: { guia_id: string; new_registro: string; new_fecha: string | null }]
  /** Emitted on successful reassign so parent can invalidate the rows query. */
  reassigned: []
}>()

const dialogRef = ref<HTMLDivElement | null>(null)

// Form state
const form = ref({ registro: '', fecha: '' })
const errors = ref<{ registro?: string; fecha?: string }>({})

// ISO date pattern
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/

const isFormValid = computed(() => {
  const r = form.value.registro.trim()
  if (!r) return false
  const f = form.value.fecha.trim()
  if (f && !ISO_DATE_RE.test(f)) return false
  return true
})

function validate(): boolean {
  errors.value = {}
  const r = form.value.registro.trim()
  if (!r) {
    errors.value.registro = 'El número de registro es obligatorio.'
    return false
  }
  const f = form.value.fecha.trim()
  if (f && !ISO_DATE_RE.test(f)) {
    errors.value.fecha = 'Formato de fecha inválido — use YYYY-MM-DD.'
    return false
  }
  return true
}

function onSubmit(): void {
  if (!validate()) return
  emit('submit', {
    guia_id: props.guiaId, // always the actual serie-numero (REV-C02 / CRITICAL-1 fix)
    new_registro: form.value.registro.trim(),
    new_fecha: form.value.fecha.trim() || null,
  })
}

function close(): void {
  if (props.isPending) return
  emit('update:modelValue', false)
}

function onBackdropClick(): void {
  close()
}

// Reset form when dialog opens; focus dialog element
watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      // Pre-fill with current row values as a convenience starting point.
      // The actual guia_id submitted is props.guiaId (not row.row_id).
      form.value = { registro: props.row?.registro ?? '', fecha: props.row?.fecha ?? '' }
      errors.value = {}
      await nextTick()
      dialogRef.value?.focus()
    }
  },
)
</script>

<style scoped>
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
  max-width: 480px;
  background-color: var(--surface-overlay);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  animation: slide-up var(--transition-normal) ease;
  overflow: hidden;
}

@keyframes slide-up {
  from { transform: translateY(12px); opacity: 0; }
  to   { transform: translateY(0);    opacity: 1; }
}

.dialog:focus {
  outline: none;
}

/* Header */
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

/* Context panel */
.dialog__context {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-4) var(--space-6);
  background-color: var(--surface-inset);
  border-bottom: 1px solid var(--border-subtle);
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

/* Warning */
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

/* Form */
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

.dialog__hint {
  font-size: var(--text-2xs);
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
  color: var(--text-tertiary);
}

.dialog__input {
  padding: var(--space-2) var(--space-3);
  background-color: var(--surface-inset);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-family: var(--font-mono);
  transition: border-color var(--transition-fast);
}

.dialog__input:focus {
  outline: none;
  border-color: var(--border-focus);
  box-shadow: var(--shadow-focus);
}

.dialog__input[aria-invalid="true"] {
  border-color: var(--action-danger);
}

.dialog__error {
  font-size: var(--text-xs);
  color: var(--action-danger);
}

/* Footer */
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

.dialog__btn-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* API error banner */
.dialog__api-error {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-6);
  background-color: rgba(248, 81, 73, 0.1);
  border-top: 1px solid rgba(248, 81, 73, 0.2);
  font-size: var(--text-xs);
  color: var(--status-mismatch-fg);
}
</style>
