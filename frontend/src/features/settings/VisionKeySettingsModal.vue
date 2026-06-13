<template>
  <Teleport to="body">
    <div
      v-if="modelValue"
      class="vision-key-modal__backdrop"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      @click.self="close"
      @keydown.escape="close"
    >
      <div ref="dialogRef" class="vision-key-modal" tabindex="-1">
        <div class="vision-key-modal__header">
          <h2 :id="titleId" class="vision-key-modal__title">Configurar IA</h2>
          <button
            class="vision-key-modal__close"
            type="button"
            aria-label="Cerrar diálogo"
            @click="close"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>

        <p class="vision-key-modal__intro">
          Pegá tu API key del proveedor de visión. La validamos contra el
          proveedor antes de guardarla; nunca se muestra ni se registra.
        </p>

        <form class="vision-key-modal__form" @submit.prevent="onSubmit" novalidate>
          <label :for="inputId" class="vision-key-modal__label">API key</label>
          <input
            :id="inputId"
            ref="inputRef"
            v-model="key"
            class="vision-key-modal__input mono"
            type="password"
            autocomplete="off"
            spellcheck="false"
            :disabled="busy"
            :aria-busy="busy"
            placeholder="••••••••••••"
          />

          <!-- Status indicator: idle (none) | saving | success | error -->
          <p
            v-if="state === 'success'"
            class="vision-key-modal__status vision-key-modal__status--success"
            role="status"
            aria-live="polite"
          >
            {{ successMessage }}
          </p>
          <p
            v-else-if="state === 'error'"
            class="vision-key-modal__status vision-key-modal__status--error"
            role="alert"
          >
            {{ errorMessage }}
          </p>

          <div class="vision-key-modal__actions">
            <button
              class="vision-key-modal__remove"
              type="button"
              :disabled="busy"
              title="Quitar la API key guardada y deshabilitar la IA"
              @click="onRemove"
            >
              Quitar key
            </button>
            <button
              class="vision-key-modal__submit"
              type="submit"
              :disabled="busy || key.trim().length === 0"
              :aria-busy="state === 'saving'"
            >
              {{ state === 'saving' ? 'Validando…' : 'Guardar y validar' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * VisionKeySettingsModal — key-only settings modal (SDD#4, VKS-004).
 *
 * Reached from a new "Ajustes / Configurar IA" item in RunHistoryMenu — NO new
 * route (D6). The flow is validate-before-persist on the backend:
 *   POST /settings/vision-key → 200 {restart_required} | 400 invalid | 503
 *   unreachable | 422 empty/over-long.
 *
 * Write-only contract (VKS-004-S04): the stored key is NEVER pre-populated; the
 * input starts empty on every open. The key is never echoed by the backend, so
 * there is nothing to display. "Quitar key" hits DELETE /settings/vision-key.
 *
 * Status is surfaced inline (no toast library exists in this app) via
 * role=status / role=alert, mirroring the PendientesPorProcesarTab convention.
 */

import { ref, watch, nextTick } from 'vue'
import { saveVisionKey, deleteVisionKey } from '@/api/client'

const props = defineProps<{
  /** v-model: modal open state. */
  modelValue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
}>()

type SaveState = 'idle' | 'saving' | 'success' | 'error'

const uid = Math.random().toString(36).slice(2, 8)
const titleId = `vision-key-title-${uid}`
const inputId = `vision-key-input-${uid}`

const key = ref('')
const state = ref<SaveState>('idle')
const errorMessage = ref('')
const successMessage = ref('')

const dialogRef = ref<HTMLDivElement | null>(null)
const inputRef = ref<HTMLInputElement | null>(null)

// Busy while a save/delete is in-flight (locks the form).
const busy = ref(false)

// Reset to a clean write-only state every time the modal opens (VKS-004-S04).
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      key.value = ''
      state.value = 'idle'
      errorMessage.value = ''
      successMessage.value = ''
      void nextTick(() => {
        dialogRef.value?.focus()
        inputRef.value?.focus()
      })
    }
  },
  { immediate: true },
)

function close(): void {
  emit('update:modelValue', false)
}

function extractError(err: unknown): string {
  if (
    typeof err === 'object' &&
    err !== null &&
    'response' in err &&
    typeof (err as { response?: { data?: { detail?: string } } }).response?.data?.detail === 'string'
  ) {
    return (err as { response: { data: { detail: string } } }).response.data.detail
  }
  if (err instanceof Error) return err.message
  return 'No se pudo validar la API key. Reintentá.'
}

async function onSubmit(): Promise<void> {
  const candidate = key.value.trim()
  if (!candidate || busy.value) return // empty guard (avoids a pointless 422)
  busy.value = true
  state.value = 'saving'
  errorMessage.value = ''
  successMessage.value = ''
  try {
    await saveVisionKey(candidate)
    state.value = 'success'
    successMessage.value = 'Key válida — reiniciá la app para activar la IA.'
    key.value = '' // clear on success (VKS-004-S01)
  } catch (err) {
    state.value = 'error'
    errorMessage.value = extractError(err)
    // keep key.value so the operator can correct it (VKS-004-S02)
  } finally {
    busy.value = false
  }
}

async function onRemove(): Promise<void> {
  if (busy.value) return
  busy.value = true
  state.value = 'saving'
  errorMessage.value = ''
  successMessage.value = ''
  try {
    await deleteVisionKey()
    state.value = 'success'
    successMessage.value = 'Key eliminada — reiniciá la app para aplicar el cambio.'
    key.value = ''
  } catch (err) {
    state.value = 'error'
    errorMessage.value = extractError(err)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.vision-key-modal__backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 1000);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-6);
  background-color: rgba(0, 0, 0, 0.5);
}

.vision-key-modal {
  width: 100%;
  max-width: 420px;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-6);
  background-color: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg, var(--radius-md));
  box-shadow: var(--shadow-lg, 0 10px 30px rgba(0, 0, 0, 0.3));
}

.vision-key-modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.vision-key-modal__title {
  margin: 0;
  font-size: var(--text-md, var(--text-base));
  font-weight: 600;
  color: var(--text-primary);
}

.vision-key-modal__close {
  display: inline-flex;
  padding: var(--space-1);
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.vision-key-modal__close:hover {
  color: var(--text-primary);
  background-color: var(--surface-hover);
}

.vision-key-modal__intro {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.vision-key-modal__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.vision-key-modal__label {
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.vision-key-modal__input {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-primary);
  background-color: var(--surface-base);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
}

.vision-key-modal__input:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.vision-key-modal__status {
  margin: 0;
  font-size: var(--text-sm);
}

.vision-key-modal__status--success {
  color: var(--text-success, #2e7d32);
}

.vision-key-modal__status--error {
  color: var(--text-danger, #c62828);
}

.vision-key-modal__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

.vision-key-modal__remove {
  font-size: var(--text-sm);
  color: var(--text-danger, #c62828);
  background: none;
  border: none;
  cursor: pointer;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
}

.vision-key-modal__remove:hover:not(:disabled) {
  background-color: var(--surface-hover);
}

.vision-key-modal__submit {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--action-primary-text, #fff);
  background-color: var(--action-primary, #1565c0);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
}

.vision-key-modal__submit:hover:not(:disabled) {
  background-color: var(--action-primary-hover, #0d47a1);
}

.vision-key-modal__submit:disabled,
.vision-key-modal__remove:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
