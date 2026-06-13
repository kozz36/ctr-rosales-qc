/**
 * RunHistoryMenu — "Ajustes" item opens the VisionKeySettingsModal (VKS-004-S03).
 *
 * The settings modal is reached from a new "Ajustes" item in the hamburger menu
 * (NO new route — D6). Clicking it opens the modal in-place.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { installLocalStorageStub } from '../test-utils/local-storage-stub'

const { pushSpy } = vi.hoisted(() => ({ pushSpy: vi.fn() }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushSpy }),
}))

vi.mock('@/api/client', () => ({
  createRun: vi.fn(),
  saveVisionKey: vi.fn(),
  deleteVisionKey: vi.fn(),
}))

import RunHistoryMenu from '@/features/run/RunHistoryMenu.vue'

function mountMenu() {
  return mount(RunHistoryMenu, {
    global: { plugins: [createPinia()] },
    attachTo: document.body,
  })
}

describe('RunHistoryMenu — Ajustes / Configurar IA (VKS-004-S03)', () => {
  beforeEach(() => {
    installLocalStorageStub()
    setActivePinia(createPinia())
    pushSpy.mockReset()
    document.body.innerHTML = ''
  })

  it('renders an "Ajustes" menu item', async () => {
    const wrapper = mountMenu()
    await wrapper.find('.run-history-menu__trigger').trigger('click')
    const labels = wrapper.findAll('[role="menuitem"]').map((i) => i.text())
    expect(labels.some((t) => /Ajustes|Configurar IA/i.test(t))).toBe(true)
    wrapper.unmount()
  })

  it('clicking "Ajustes" opens the VisionKeySettingsModal (no navigation)', async () => {
    const wrapper = mountMenu()
    await wrapper.find('.run-history-menu__trigger').trigger('click')
    const ajustes = wrapper
      .findAll('[role="menuitem"]')
      .find((i) => /Ajustes|Configurar IA/i.test(i.text()))!
    await ajustes.trigger('click')
    await wrapper.vm.$nextTick()

    // Modal is teleported to body
    expect(document.querySelector('.vision-key-modal')).not.toBeNull()
    // No route navigation for settings (D6)
    expect(pushSpy).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
