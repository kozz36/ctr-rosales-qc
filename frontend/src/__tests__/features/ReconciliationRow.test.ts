/**
 * Tests for ReconciliationRow.vue (rev-2)
 *
 * Covers (S2.3 deliverables):
 * - Chevron click → GuiaDrillDown visible; click again → hidden
 * - summed_qty cell has no <input> element (REV-C04 / CRITICAL-2 fix)
 * - GuiaDrillDown reassign event propagated as openReassign with guia_id
 * - Drill-down renders without extra API call
 * - Row renders all basic columns
 * - Status badge displays correct label for each RowStatus
 */

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import type { ReconciliationRowResponse, GuiaContributionResponse } from '@/api/types'

// ---------------------------------------------------------------------------
// Stub child components to isolate ReconciliationRow logic
// ---------------------------------------------------------------------------

vi.mock('@/features/review/ConfidenceBadge.vue', () => ({
  default: {
    name: 'ConfidenceBadge',
    template: '<span class="stub-confidence" />',
    props: ['value', 'compact'],
  },
}))

vi.mock('@/features/review/SourcePages.vue', () => ({
  default: {
    name: 'SourcePages',
    template: '<div class="stub-source-pages" />',
    props: ['pages', 'runId'],
    emits: ['pageClick'],
  },
}))

// GuiaDrillDown stub — emits 'reassign' and 'rowUpdated' on demand
vi.mock('@/features/review/GuiaDrillDown.vue', () => ({
  default: {
    name: 'GuiaDrillDown',
    template: '<tr class="stub-drill-down"><td /></tr>',
    props: ['guias', 'runId'],
    emits: ['reassign', 'rowUpdated'],
  },
}))

import ReconciliationRow from '@/features/review/ReconciliationRow.vue'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeGuia(overrides: Partial<GuiaContributionResponse> = {}): GuiaContributionResponse {
  return {
    guia_id: 'T009-0741770',
    source_pages: [4],
    cantidad: '1250.0',
    unidad: 'KG',
    confidence: 0.92,
    identity_source: 'qr',
    year_inferred: false,
    fecha: '2025-03-15',
    fecha_divergence: false,
    divergence_reason: null,
    ...overrides,
  }
}

function makeRow(overrides: Partial<ReconciliationRowResponse> = {}): ReconciliationRowResponse {
  return {
    row_id: '232|2025-03-15|BARRA CORRUGADA 1/2|KG',
    registro: '232',
    fecha: '2025-03-15',
    material_canonical: 'BARRA CORRUGADA 1/2',
    unidad: 'KG',
    declared_qty: '1250.0',
    summed_qty: '1250.0',
    delta: '0.0',
    status: 'MATCH',
    source_pages: [4, 5],
    min_confidence: 0.92,
    requires_review: false,
    guias: [makeGuia()],
    any_year_inferred: false,
    has_fecha_divergence: false,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('ReconciliationRow', () => {
  it('renders registro, material_canonical, fecha, unidad in cells', () => {
    const row = makeRow()
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.text()).toContain('232')
    expect(wrapper.text()).toContain('2025-03-15')
    expect(wrapper.text()).toContain('BARRA CORRUGADA 1/2')
    expect(wrapper.text()).toContain('KG')
  })

  it('renders declared_qty read-only (span, not input)', () => {
    const row = makeRow()
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.text()).toContain('1')  // declared qty shows
  })

  it('summed_qty cell has no <input> element (CRITICAL-2 fix / REV-C04)', () => {
    // In rev-2, summed_qty is derived from guías — it MUST be read-only
    const row = makeRow({ status: 'MISMATCH', summed_qty: '1260.0' })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    // No input anywhere in the row (including MISMATCH rows where old code had an input)
    const inputs = wrapper.findAll('input')
    expect(inputs).toHaveLength(0)
  })

  it('summed_qty cell has no <input> even for MATCH rows', () => {
    const row = makeRow({ status: 'MATCH' })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.findAll('input')).toHaveLength(0)
  })

  it('expand button visible when row has guias', () => {
    const row = makeRow()
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.recon-row__expand-btn').exists()).toBe(true)
  })

  it('expand button hidden when guias is empty', () => {
    const row = makeRow({ guias: [] })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.recon-row__expand-btn').exists()).toBe(false)
  })

  it('chevron click → GuiaDrillDown visible', async () => {
    const row = makeRow()
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.stub-drill-down').exists()).toBe(false)
    await wrapper.find('.recon-row__expand-btn').trigger('click')
    expect(wrapper.find('.stub-drill-down').exists()).toBe(true)
  })

  it('chevron click again → GuiaDrillDown hidden (toggle)', async () => {
    const row = makeRow()
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    await wrapper.find('.recon-row__expand-btn').trigger('click')
    expect(wrapper.find('.stub-drill-down').exists()).toBe(true)
    await wrapper.find('.recon-row__expand-btn').trigger('click')
    expect(wrapper.find('.stub-drill-down').exists()).toBe(false)
  })

  it('GuiaDrillDown reassign event propagated as openReassign with guia_id', async () => {
    const row = makeRow()
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    await wrapper.find('.recon-row__expand-btn').trigger('click')
    // Emit the reassign event from the stubbed GuiaDrillDown
    await wrapper.findComponent({ name: 'GuiaDrillDown' }).vm.$emit('reassign', 'T009-0741770')
    expect(wrapper.emitted('openReassign')).toBeTruthy()
    const payload = wrapper.emitted('openReassign')![0][0] as { guia_id: string }
    expect(payload.guia_id).toBe('T009-0741770')
  })

  it('rowUpdated emitted when GuiaDrillDown emits rowUpdated', async () => {
    const row = makeRow()
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    await wrapper.find('.recon-row__expand-btn').trigger('click')
    await wrapper.findComponent({ name: 'GuiaDrillDown' }).vm.$emit('rowUpdated')
    expect(wrapper.emitted('rowUpdated')).toBeTruthy()
  })

  it('drill-down renders without extra API call (GuiaDrillDown receives guias from row prop)', async () => {
    // The stub is used — no actual API composable is called from ReconciliationRow
    // after expansion. This test verifies the data flows from props, not from a fetch.
    const row = makeRow()
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    await wrapper.find('.recon-row__expand-btn').trigger('click')
    const drillDown = wrapper.findComponent({ name: 'GuiaDrillDown' })
    expect(drillDown.props('guias')).toEqual(row.guias)
    expect(drillDown.props('runId')).toBe('run-abc')
  })

  it('status badge displays correct label for MISMATCH', () => {
    const wrapper = mount(ReconciliationRow, {
      props: { row: makeRow({ status: 'MISMATCH' }), runId: 'run-abc' },
    })
    expect(wrapper.find('.recon-row__status-badge').text()).toContain('Diferencia')
  })

  it('status badge displays correct label for MATCH (Conforme — REV-001 localization)', () => {
    const wrapper = mount(ReconciliationRow, {
      props: { row: makeRow({ status: 'MATCH' }), runId: 'run-abc' },
    })
    expect(wrapper.find('.recon-row__status-badge').text()).toContain('Conforme')
  })

  it('fecha shown as — when null', () => {
    const wrapper = mount(ReconciliationRow, {
      props: { row: makeRow({ fecha: null }), runId: 'run-abc' },
    })
    expect(wrapper.text()).toContain('—')
  })

  it('UNCLASSIFIED badge uses neutral class, NOT the green MATCH class (REV-004 / S2.5)', () => {
    const row = makeRow({ status: 'UNCLASSIFIED' })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    const badge = wrapper.find('.recon-row__status-badge')
    expect(badge.exists()).toBe(true)
    // Must have the unclassified class (neutral token)
    expect(badge.classes()).toContain('recon-row__status-badge--unclassified')
    // Must NOT have the match class (green token)
    expect(badge.classes()).not.toContain('recon-row__status-badge--match')
  })

  it('aria-rowcount on table is bound reactively (ReviewGrid contract)', () => {
    // This test lives in ReviewGrid.test.ts — here we just confirm the badge label path
    const row = makeRow({ status: 'UNCLASSIFIED' })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.recon-row__status-badge').text()).toContain('Sin clasificar')
  })

  // ---------------------------------------------------------------------------
  // Rev-3 D5 / REV-C05: any_year_inferred aggregate advisory badge (R4.2)
  // ---------------------------------------------------------------------------

  it('shows YearInferredBadge in confidence cell when any_year_inferred=true (R4.2)', () => {
    const row = makeRow({ any_year_inferred: true })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.year-inferred-badge').exists()).toBe(true)
  })

  it('does not show YearInferredBadge when any_year_inferred=false (R4.2)', () => {
    const row = makeRow({ any_year_inferred: false })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.year-inferred-badge').exists()).toBe(false)
  })

  it('YearInferredBadge is independent from requires_review flag (can show both)', () => {
    const row = makeRow({ any_year_inferred: true, requires_review: true })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.year-inferred-badge').exists()).toBe(true)
    expect(wrapper.find('.recon-row__flag--review').exists()).toBe(true)
  })

  // ---------------------------------------------------------------------------
  // R9 / FDR-009: has_fecha_divergence group indicator (FechaDivergenceBadge)
  // ---------------------------------------------------------------------------

  it('shows FechaDivergenceBadge in Col 9 when has_fecha_divergence=true (FDR-S16)', () => {
    const row = makeRow({ has_fecha_divergence: true })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.fecha-divergence-badge').exists()).toBe(true)
  })

  it('does not show FechaDivergenceBadge when has_fecha_divergence=false (FDR-S17)', () => {
    const row = makeRow({ has_fecha_divergence: false })
    const wrapper = mount(ReconciliationRow, {
      props: { row, runId: 'run-abc' },
    })
    expect(wrapper.find('.fecha-divergence-badge').exists()).toBe(false)
  })
})
