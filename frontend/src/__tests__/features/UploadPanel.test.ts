/**
 * UploadPanel component — unit tests
 *
 * Covers:
 *  - Renders drop zone
 *  - Validates non-PDF rejection (shows error, does not call store.upload)
 *  - Validates oversized file rejection
 *  - Shows uploading state while store.uploading is true
 *  - Shows run_id after successful upload
 *  - Shows error from store.error
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mock the API client so no HTTP is issued
vi.mock('@/api/client', () => ({
  createRun: vi.fn(),
}))

// Stub RunProgress so we isolate UploadPanel tests
vi.mock('@/features/run/RunProgress.vue', () => ({
  default: {
    name: 'RunProgress',
    template: '<div data-testid="run-progress-stub" />',
    props: ['runId'],
    emits: ['completed', 'failed'],
  },
}))

import UploadPanel from '@/features/run/UploadPanel.vue'
import { useRunStore } from '@/stores/run'
import { createRun } from '@/api/client'

const mockCreateRun = vi.mocked(createRun)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeFile(name: string, type: string, size = 512): File {
  const blob = new Blob([new Uint8Array(size)], { type })
  return new File([blob], name, { type })
}

function buildWrapper() {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', name: 'upload', component: UploadPanel },
      { path: '/runs/:id', name: 'review', component: { template: '<div />' } },
    ],
  })

  return mount(UploadPanel, {
    global: {
      plugins: [createPinia(), router],
    },
  })
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('UploadPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockCreateRun.mockReset()
  })

  it('renders the drop zone', () => {
    const wrapper = buildWrapper()
    expect(wrapper.find('.upload-zone').exists()).toBe(true)
    expect(wrapper.find('h1').text()).toContain('Reconciliación de materiales')
  })

  it('shows validation error for non-PDF file (no API call)', async () => {
    const wrapper = buildWrapper()
    const input = wrapper.find<HTMLInputElement>('input[type="file"]')

    const file = makeFile('data.xlsx', 'application/vnd.ms-excel')
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.find('.upload-panel__error').text()).toContain('El archivo debe ser un PDF.')
    expect(mockCreateRun).not.toHaveBeenCalled()
  })

  it('shows validation error for oversized PDF (no API call)', async () => {
    const wrapper = buildWrapper()
    const input = wrapper.find<HTMLInputElement>('input[type="file"]')

    // Override size property on File to exceed 100 MB
    const base = makeFile('huge.pdf', 'application/pdf', 10)
    const bigFile = Object.defineProperty(base, 'size', {
      value: 100 * 1024 * 1024 + 1,
    }) as File

    Object.defineProperty(input.element, 'files', {
      value: [bigFile],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.find('.upload-panel__error').text()).toContain(
      'El archivo excede el límite de 100 MB.',
    )
    expect(mockCreateRun).not.toHaveBeenCalled()
  })

  it('shows run_id after successful upload', async () => {
    mockCreateRun.mockResolvedValueOnce({ run_id: 'abc-123', status: 'pending' })

    const wrapper = buildWrapper()
    const input = wrapper.find<HTMLInputElement>('input[type="file"]')

    const file = makeFile('plan.pdf', 'application/pdf')
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.find('.upload-panel__run-id-value').text()).toContain('abc-123')
    expect(wrapper.find('[data-testid="run-progress-stub"]').exists()).toBe(true)
  })

  it('shows network error from run store', async () => {
    mockCreateRun.mockRejectedValueOnce(new Error('Connection refused'))

    const wrapper = buildWrapper()
    const input = wrapper.find<HTMLInputElement>('input[type="file"]')

    const file = makeFile('plan.pdf', 'application/pdf')
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.find('.upload-panel__error').text()).toContain('Connection refused')
  })

  it('does not open file picker while uploading', () => {
    const wrapper = buildWrapper()
    const store = useRunStore()
    store.uploading = true

    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click')
    void wrapper.find('.upload-zone').trigger('click')

    expect(clickSpy).not.toHaveBeenCalled()
  })
})
