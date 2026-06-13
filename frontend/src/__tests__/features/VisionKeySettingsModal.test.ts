/**
 * VisionKeySettingsModal — key-only settings modal (VKS-004).
 *
 * Covers:
 * - Renders a masked (type=password) key input + "Guardar y validar" submit.
 * - Submitting calls saveVisionKey(key); success → restart-required notice +
 *   key field cleared (VKS-004-S01).
 * - Failure (400/503/422) → error message surfaced; key NOT cleared so the user
 *   can correct it (VKS-004-S02).
 * - Never pre-populates the stored key — the input starts empty every open
 *   (VKS-004-S04).
 * - "Quitar key" action calls deleteVisionKey().
 * - Closing the modal emits update:modelValue=false.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const { mockSave, mockDelete } = vi.hoisted(() => ({
  mockSave: vi.fn(),
  mockDelete: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  saveVisionKey: (...a: unknown[]) => mockSave(...a),
  deleteVisionKey: (...a: unknown[]) => mockDelete(...a),
}))

import VisionKeySettingsModal from '@/features/settings/VisionKeySettingsModal.vue'

function mountOpen() {
  return mount(VisionKeySettingsModal, {
    props: { modelValue: true },
    attachTo: document.body,
  })
}

function keyInput(): HTMLInputElement {
  return document.querySelector('.vision-key-modal__input') as HTMLInputElement
}

function setKey(value: string): void {
  const el = keyInput()
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('VisionKeySettingsModal (VKS-004)', () => {
  beforeEach(() => {
    mockSave.mockReset()
    mockDelete.mockReset()
    document.body.innerHTML = ''
  })

  it('renders a masked password key input and a submit button', () => {
    const wrapper = mountOpen()
    const input = keyInput()
    expect(input).not.toBeNull()
    expect(input.getAttribute('type')).toBe('password')
    const submit = document.querySelector('.vision-key-modal__submit')
    expect(submit).not.toBeNull()
    wrapper.unmount()
  })

  it('does NOT render base_url or model fields (key-only modal)', () => {
    const wrapper = mountOpen()
    const html = document.body.innerHTML.toLowerCase()
    expect(html).not.toContain('base_url')
    expect(html).not.toContain('base url')
    expect(html).not.toContain('modelo')
    wrapper.unmount()
  })

  it('valid key → saveVisionKey called, success notice shown, field cleared (VKS-004-S01)', async () => {
    mockSave.mockResolvedValue({ restart_required: true })
    const wrapper = mountOpen()
    setKey('sk-valid-123')
    await wrapper.vm.$nextTick()
    ;(document.querySelector('.vision-key-modal__submit') as HTMLButtonElement).click()
    await flushPromises()

    expect(mockSave).toHaveBeenCalledWith('sk-valid-123')
    const success = document.querySelector('.vision-key-modal__status--success')
    expect(success).not.toBeNull()
    expect(success!.textContent ?? '').toMatch(/reinici/i)
    // field cleared on success
    expect(keyInput().value).toBe('')
    wrapper.unmount()
  })

  it('invalid key (rejected) → error message shown, key NOT cleared (VKS-004-S02)', async () => {
    mockSave.mockRejectedValue({ response: { status: 400, data: { detail: 'Clave inválida' } } })
    const wrapper = mountOpen()
    setKey('sk-bad')
    await wrapper.vm.$nextTick()
    ;(document.querySelector('.vision-key-modal__submit') as HTMLButtonElement).click()
    await flushPromises()

    const error = document.querySelector('.vision-key-modal__status--error')
    expect(error).not.toBeNull()
    expect(error!.textContent ?? '').toContain('Clave inválida')
    // not cleared so the operator can fix it
    expect(keyInput().value).toBe('sk-bad')
    wrapper.unmount()
  })

  it('does not call saveVisionKey when the key is empty (guard / VKS-004 422 avoidance)', async () => {
    const wrapper = mountOpen()
    ;(document.querySelector('.vision-key-modal__submit') as HTMLButtonElement).click()
    await flushPromises()
    expect(mockSave).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('never pre-populates a stored key — input is empty on open (VKS-004-S04)', () => {
    const wrapper = mountOpen()
    expect(keyInput().value).toBe('')
    wrapper.unmount()
  })

  it('"Quitar key" calls deleteVisionKey() and shows the restart notice', async () => {
    mockDelete.mockResolvedValue({ restart_required: true })
    const wrapper = mountOpen()
    ;(document.querySelector('.vision-key-modal__remove') as HTMLButtonElement).click()
    await flushPromises()
    expect(mockDelete).toHaveBeenCalledOnce()
    const success = document.querySelector('.vision-key-modal__status--success')
    expect(success).not.toBeNull()
    wrapper.unmount()
  })

  it('closing emits update:modelValue=false', async () => {
    const wrapper = mountOpen()
    ;(document.querySelector('.vision-key-modal__close') as HTMLButtonElement).click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
    wrapper.unmount()
  })
})
