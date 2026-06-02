<template>
  <span
    class="year-inferred-badge"
    :class="{ 'year-inferred-badge--compact': compact }"
    :title="tooltip"
    :aria-label="ariaLabel"
    role="img"
  >
    <span class="year-inferred-badge__icon" aria-hidden="true">△</span>
    <span class="year-inferred-badge__label">{{ compact ? '' : 'Año inferido' }}</span>
  </span>
</template>

<script setup lang="ts">
/**
 * YearInferredBadge — yellow advisory badge for year-inferred reception dates.
 *
 * Design contract (D5 / REV-C05):
 * - Distinct YELLOW advisory — NOT a red error, NOT a requires_review flag.
 * - A11y: state conveyed by icon (△) + label, not color alone (WCAG 1.4.1).
 * - Tooltip explains the year was reconstructed from delivery bounds, not read
 *   directly from the stamp (EXT-021).
 * - compact=true: shows icon only (label hidden); tooltip always present.
 */

const props = withDefaults(
  defineProps<{
    /** If true, compact mode — icon only, no "Año inferido" label */
    compact?: boolean
  }>(),
  { compact: false },
)

const tooltip =
  'Año inferido: el año de la fecha de recepción fue reconstruido ' +
  'a partir de los límites de entrega (fecha impresa GRE). ' +
  'El día y mes fueron leídos del sello por visión. ' +
  'Confirme o corrija la fecha si es necesario.'

const ariaLabel = props.compact
  ? 'año inferido (reconstruido por límites de entrega)'
  : 'año inferido: reconstruido por límites de entrega'
</script>

<style scoped>
.year-inferred-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px var(--space-2);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 500;
  white-space: nowrap;
  border: 1px solid rgba(227, 179, 65, 0.35);
  background-color: rgba(227, 179, 65, 0.12);
  color: var(--confidence-low);
}

.year-inferred-badge__icon {
  font-size: 0.6rem;
  line-height: 1;
}

.year-inferred-badge__label {
  font-size: var(--text-xs);
  letter-spacing: 0.02em;
}

/* In compact mode the label text is empty — only the icon shows */
.year-inferred-badge--compact .year-inferred-badge__label {
  display: none;
}
</style>
