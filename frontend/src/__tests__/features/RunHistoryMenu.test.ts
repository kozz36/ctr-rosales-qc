/**
 * RunHistoryMenu — header hamburger menu (SDD#3 PR-3, RH-010).
 *
 * Three sections: [Nuevo batch] (reset store + nav to upload),
 * [Batch actual] (nav to /runs/{runStore.runId}; disabled when no active run),
 * [Historial] (nav to /historial).
 *
 * Router is mocked module-level (useRouter → push spy); the store is the real
 * Pinia run store so reset() semantics are exercised, not stubbed.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useRunStore } from '@/stores/run'
import { installLocalStorageStub } from '../test-utils/local-storage-stub'

const { pushSpy } = vi.hoisted(() => ({ pushSpy: vi.fn() }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushSpy }),
}))

vi.mock('@/api/client', () => ({
  createRun: vi.fn(),
}))

import RunHistoryMenu from '@/features/run/RunHistoryMenu.vue'

function mountMenu() {
  return mount(RunHistoryMenu, {
    global: { plugins: [createPinia()] },
  })
}

async function openMenu(wrapper: ReturnType<typeof mountMenu>) {
  await wrapper.find('.run-history-menu__trigger').trigger('click')
}

describe('RunHistoryMenu (RH-010)', () => {
  beforeEach(() => {
    installLocalStorageStub()
    setActivePinia(createPinia())
    pushSpy.mockReset()
  })

  it('renders three menu sections: Nuevo batch, Batch actual, Historial', async () => {
    const wrapper = mountMenu()
    await openMenu(wrapper)

    const items = wrapper.findAll('[role="menuitem"]')
    const labels = items.map((i) => i.text())
    expect(labels).toHaveLength(3)
    expect(labels.some((t) => t.includes('Nuevo batch'))).toBe(true)
    expect(labels.some((t) => t.includes('Batch actual'))).toBe(true)
    expect(labels.some((t) => t.includes('Historial'))).toBe(true)
  })

  it('Nuevo batch resets store and navigates to upload page (RH-010-S01)', async () => {
    const wrapper = mountMenu()
    const store = useRunStore()
    store.runId = 'run-active'
    store.setStatus('review')

    await openMenu(wrapper)
    const nuevo = wrapper
      .findAll('[role="menuitem"]')
      .find((i) => i.text().includes('Nuevo batch'))!
    await nuevo.trigger('click')

    expect(store.runId).toBeNull()
    expect(store.status).toBeNull()
    expect(pushSpy).toHaveBeenCalledWith('/')
  })

  it('Batch actual is disabled when no run is active (RH-010-S04)', async () => {
    const wrapper = mountMenu()
    const store = useRunStore()
    expect(store.runId).toBeNull()

    await openMenu(wrapper)
    const batchActual = wrapper
      .findAll('[role="menuitem"]')
      .find((i) => i.text().includes('Batch actual'))!
    expect(batchActual.attributes('disabled')).toBeDefined()

    await batchActual.trigger('click')
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('Batch actual navigates to the current run when active', async () => {
    const wrapper = mountMenu()
    const store = useRunStore()
    store.runId = 'abc'

    await openMenu(wrapper)
    const batchActual = wrapper
      .findAll('[role="menuitem"]')
      .find((i) => i.text().includes('Batch actual'))!
    expect(batchActual.attributes('disabled')).toBeUndefined()
    await batchActual.trigger('click')

    expect(pushSpy).toHaveBeenCalledWith('/runs/abc')
  })

  it('Historial navigates to the /historial route', async () => {
    const wrapper = mountMenu()
    await openMenu(wrapper)
    const historial = wrapper
      .findAll('[role="menuitem"]')
      .find((i) => i.text().includes('Historial'))!
    await historial.trigger('click')

    expect(pushSpy).toHaveBeenCalledWith('/historial')
  })
})
