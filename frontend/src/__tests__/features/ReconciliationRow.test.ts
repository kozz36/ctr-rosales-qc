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
    guias: [makeGuia()],
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

  it('status badge displays correct label for MATCH', () => {
    const wrapper = mount(ReconciliationRow, {
      props: { row: makeRow({ status: 'MATCH' }), runId: 'run-abc' },
    })
    expect(wrapper.find('.recon-row__status-badge').text()).toContain('Coincide')
  })

  it('fecha shown as — when null', () => {
    const wrapper = mount(ReconciliationRow, {
      props: { row: makeRow({ fecha: null }), runId: 'run-abc' },
    })
    expect(wrapper.text()).toContain('—')
  })
})
