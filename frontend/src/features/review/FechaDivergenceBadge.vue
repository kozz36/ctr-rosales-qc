<template>
  <span
    class="fecha-divergence-badge"
    :class="{ 'fecha-divergence-badge--compact': compact }"
    :title="tooltip"
    :aria-label="ariaLabel"
    role="img"
  >
    <span class="fecha-divergence-badge__icon" aria-hidden="true">⚠</span>
    <span class="fecha-divergence-badge__label">{{ compact ? '' : 'Fecha no coincide' }}</span>
  </span>
</template>

<script setup lang="ts">
/**
 * FechaDivergenceBadge — RED divergence indicator for a guía whose handwritten
 * reception date does not match the registro's declared date (R9 / ADR-8).
 *
 * Design contract (FDR-009 / #2709):
 * - RED (mismatch tokens) — distinct from the YELLOW YearInferredBadge advisory.
 *   A divergence is a misfiled-guía signal requiring human review, not inference noise.
 * - A11y: state conveyed by icon (⚠) + label, not color alone (WCAG 1.4.1).
 * - Tooltip explains the divergence and points the engineer to verify the filing.
 * - compact=true: shows icon only (label hidden); tooltip always present.
 */

const props = withDefaults(
  defineProps<{
    /** If true, compact mode — icon only, no "Fecha no coincide" label */
    compact?: boolean
  }>(),
  { compact: false },
)

const tooltip =
  'Fecha no coincide: la fecha de recepción de esta guía difiere de la fecha ' +
  'declarada en el Protocolo. Verifique si esta guía está archivada en el ' +
  'registro correcto.'

const ariaLabel = props.compact
  ? 'fecha no coincide (posible guía mal archivada)'
  : 'fecha no coincide: posible guía mal archivada'
</script>

<style scoped>
.fecha-divergence-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px var(--space-2);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 500;
  white-space: nowrap;
  border: 1px solid var(--status-mismatch-glow);
  background-color: var(--status-mismatch-bg);
  color: var(--status-mismatch-fg);
}

.fecha-divergence-badge__icon {
  font-size: 0.7rem;
  line-height: 1;
}

.fecha-divergence-badge__label {
  font-size: var(--text-xs);
  letter-spacing: 0.02em;
}

/* In compact mode the label text is empty — only the icon shows */
.fecha-divergence-badge--compact .fecha-divergence-badge__label {
  display: none;
}
</style>
