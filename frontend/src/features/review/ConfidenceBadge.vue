<template>
  <span
    class="confidence-badge"
    :class="badgeClass"
    :title="tooltip"
    :aria-label="ariaLabel"
    role="status"
  >
    <span class="confidence-badge__icon" aria-hidden="true">{{ icon }}</span>
    <span class="confidence-badge__value mono">{{ displayValue }}</span>
    <span v-if="needsReview" class="confidence-badge__label">revisar</span>
  </span>
</template>

<script setup lang="ts">
/**
 * ConfidenceBadge — displays the min_confidence score for a reconciliation row.
 *
 * Threshold: 0.85 (locked per AppConfig). Below threshold → "needs review" visual state.
 * null confidence means digital-text extraction (trusted; visually distinct).
 *
 * A11y: state conveyed by icon + label, not color alone (WCAG 1.4.1 non-text contrast).
 */

import { computed } from 'vue'

const THRESHOLD = 0.85

const props = withDefaults(
  defineProps<{
    /** null = digital text (confidence=1.0, no OCR) */
    value: number | null
    /** If true, compact mode — no "revisar" label */
    compact?: boolean
  }>(),
  { compact: false },
)

const needsReview = computed(() =>
  props.value !== null && props.value < THRESHOLD,
)

const isTrusted = computed(() => props.value === null)

/** Display: null → "—" (trusted), otherwise 0–100 integer */
const displayValue = computed(() => {
  if (props.value === null) return '—'
  return `${Math.round(props.value * 100)}%`
})

const icon = computed(() => {
  if (isTrusted.value) return '●'
  if (needsReview.value) return '!'
  return '✓'
})

const tooltip = computed(() => {
  if (isTrusted.value) return 'Extracción digital — confianza completa'
  if (needsReview.value)
    return `Confianza ${displayValue.value} — por debajo del umbral 85%, requiere revisión`
  return `Confianza ${displayValue.value} — aceptable`
})

const ariaLabel = computed(() => {
  if (isTrusted.value) return 'confianza: digital, confianza completa'
  if (needsReview.value)
    return `confianza: ${displayValue.value}, requiere revisión`
  return `confianza: ${displayValue.value}`
})

const badgeClass = computed(() => ({
  'confidence-badge--trusted': isTrusted.value,
  'confidence-badge--ok': !isTrusted.value && !needsReview.value,
  'confidence-badge--low': needsReview.value,
  'confidence-badge--compact': props.compact,
}))
</script>

<style scoped>
.confidence-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px var(--space-2);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 500;
  white-space: nowrap;
  border: 1px solid transparent;
  transition: opacity var(--transition-fast);
}

.confidence-badge__icon {
  font-size: 0.6rem;
  line-height: 1;
}

.confidence-badge__value {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.confidence-badge__label {
  font-size: var(--text-2xs);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

/* States */
.confidence-badge--trusted {
  color: var(--confidence-trusted);
  border-color: rgba(88, 166, 255, 0.25);
  background-color: rgba(88, 166, 255, 0.08);
}

.confidence-badge--ok {
  color: var(--confidence-ok);
  border-color: rgba(63, 185, 80, 0.25);
  background-color: rgba(63, 185, 80, 0.08);
}

.confidence-badge--low {
  color: var(--confidence-low);
  border-color: rgba(227, 179, 65, 0.35);
  background-color: rgba(227, 179, 65, 0.12);
  animation: pulse-low 2s ease-in-out infinite;
}

/* Only animate on state change, not on load */
@keyframes pulse-low {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.72; }
}

.confidence-badge--compact .confidence-badge__label {
  display: none;
}
</style>
