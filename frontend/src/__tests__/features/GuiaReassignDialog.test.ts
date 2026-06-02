/**
 * Tests for GuiaReassignDialog.vue
 *
 * Covers:
 * - Dialog hidden when modelValue=false
 * - Dialog visible when modelValue=true
 * - Shows current row context (registro, fecha, material, status)
 * - Submit button disabled when registro is empty
 * - Submit with valid registro emits submit event
 * - Submit with invalid fecha format shows validation error
 * - Close button emits update:modelValue=false
 * - Escape key closes dialog
 * - isPending disables buttons and shows spinner
 * - apiError banner shown when apiError prop is set
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import GuiaReassignDialog from '@/features/review/GuiaReassignDialog.vue'
import type { ReconciliationRowResponse } from '@/api/types'

// Default guiaId for tests — represents the actual serie-numero (rev-2 CRITICAL-1 fix)
const TEST_GUIA_ID = 'T009-0741770'

/**
 * Teleport renders content into document.body by default.
 * Vue Test Utils 2.x does not resolve Teleport targets automatically in jsdom
 * unless we stub Teleport or query document.body directly.
 *
 * Strategy: stub Teleport as a passthrough so the dialog renders inline in
 * the wrapper's DOM tree. This is the recommended VTU approach for Teleport.
 */
const GLOBAL_STUBS = {
  Teleport: { template: '<slot />' },
}

function makeRow(): ReconciliationRowResponse {
  return {
    row_id: 'r1|2024-01-15|BARRAS|KG',
    registro: 'r1',
    fecha: '2024-01-15',
    material_canonical: 'BARRAS DE ACERO',
    unidad: 'KG',
    declared_qty: '1000',
    summed_qty: '1010',
    delta: '10',
    status: 'MISMATCH',
    source_pages: [3],
    min_confidence: 0.88,
    requires_review: false,
    guias: [],
  }
}

describe('GuiaReassignDialog', () => {
  it('is not rendered when modelValue=false', () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: false, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    expect(wrapper.find('.dialog').exists()).toBe(false)
  })

  it('renders dialog when modelValue=true', () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    expect(wrapper.find('.dialog').exists()).toBe(true)
  })

  it('shows current registro and fecha in context', () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    expect(wrapper.find('.dialog__context').text()).toContain('r1')
    expect(wrapper.find('.dialog__context').text()).toContain('2024-01-15')
  })

  it('submit button is disabled when registro input is empty', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    const registroInput = wrapper.find('input[type="text"]')
    await registroInput.setValue('')
    const submitBtn = wrapper.find('button[type="submit"]')
    expect(submitBtn.attributes('disabled')).toBeDefined()
  })

  it('emits submit with correct payload on valid form', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    const inputs = wrapper.findAll('input[type="text"]')
    // First input is registro, second is fecha
    await inputs[0].setValue('r2')
    await inputs[1].setValue('2024-02-01')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('submit')).toBeTruthy()
    const payload = wrapper.emitted('submit')![0][0] as { guia_id: string; new_registro: string; new_fecha: string | null }
    // Rev-2 CRITICAL-1 fix: guia_id must be the actual serie-numero, not row_id
    expect(payload.guia_id).toBe(TEST_GUIA_ID)
    expect(payload.new_registro).toBe('r2')
    expect(payload.new_fecha).toBe('2024-02-01')
  })

  it('emits submit with null fecha when fecha field is empty', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    const inputs = wrapper.findAll('input[type="text"]')
    await inputs[0].setValue('r99')
    await inputs[1].setValue('')
    await wrapper.find('form').trigger('submit')
    const payload = wrapper.emitted('submit')![0][0] as { new_fecha: string | null }
    expect(payload.new_fecha).toBeNull()
  })

  it('shows validation error for invalid fecha format', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    const inputs = wrapper.findAll('input[type="text"]')
    await inputs[0].setValue('r2')
    await inputs[1].setValue('not-a-date')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('.dialog__error').exists()).toBe(true)
    expect(wrapper.find('.dialog__error').text()).toContain('YYYY-MM-DD')
    expect(wrapper.emitted('submit')).toBeFalsy()
  })

  it('emits update:modelValue=false when close button clicked', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    await wrapper.find('.dialog__close').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
  })

  it('emits update:modelValue=false when Escape is pressed on backdrop', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    await wrapper.find('.dialog-backdrop').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
  })

  it('disables submit button when isPending=true', () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow(), isPending: true },
      global: { stubs: GLOBAL_STUBS },
    })
    const primaryBtn = wrapper.find('button[type="submit"]')
    expect(primaryBtn.attributes('disabled')).toBeDefined()
  })

  it('shows apiError banner when apiError prop is set', () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow(), apiError: 'Registro no encontrado' },
      global: { stubs: GLOBAL_STUBS },
    })
    expect(wrapper.find('.dialog__api-error').exists()).toBe(true)
    expect(wrapper.find('.dialog__api-error').text()).toContain('Registro no encontrado')
  })

  it('shows warning about fecha changing row groups', () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    expect(wrapper.find('.dialog__warning').exists()).toBe(true)
    expect(wrapper.find('.dialog__warning').text()).toContain('fecha')
  })

  it('cancel button emits update:modelValue=false', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: TEST_GUIA_ID, row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    const cancelBtn = wrapper.find('.dialog__btn--secondary')
    await cancelBtn.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
  })

  // ---------------------------------------------------------------------------
  // Rev-2 S2.4 tests — guia_id identity fix (REV-C02 / CRITICAL-1)
  // ---------------------------------------------------------------------------

  it('displays guiaId (serie-numero) in dialog context header', () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: 'T009-0741770', row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    expect(wrapper.find('.dialog__context').text()).toContain('T009-0741770')
  })

  it('sends guia_id (not row_id) as identifier in submit payload', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: 'T073-0680256', row: makeRow() },
      global: { stubs: GLOBAL_STUBS },
    })
    const inputs = wrapper.findAll('input[type="text"]')
    await inputs[0].setValue('231')
    await wrapper.find('form').trigger('submit')
    const payload = wrapper.emitted('submit')![0][0] as { guia_id: string }
    // CRITICAL-1: must be the actual serie-numero, never the compound row_id
    expect(payload.guia_id).toBe('T073-0680256')
    expect(payload.guia_id).not.toContain('|') // row_id always contains '|'
  })

  it('works when row is null (guía from unresolved bucket)', async () => {
    const wrapper = mount(GuiaReassignDialog, {
      props: { modelValue: true, guiaId: 'T009-0741770', row: null },
      global: { stubs: GLOBAL_STUBS },
    })
    // Should render without crash and show guiaId
    expect(wrapper.find('.dialog').exists()).toBe(true)
    expect(wrapper.find('.dialog__context').text()).toContain('T009-0741770')
  })
})
