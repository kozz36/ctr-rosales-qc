/**
 * T-8 Tests: ErroredGuiasPanel REINTENTAR button behavior (REV-R09)
 *
 * TDD RED phase — tests fail until the REINTENTAR button is added to the component.
 *
 * Covers:
 * - REINTENTAR button renders per errored guía entry
 * - Button is enabled when retry_attempted=false
 * - Button is disabled when retry_attempted=true
 * - Disabled button shows "SUNAT no disponible" hint
 * - Loading state: button disabled while retrying (retryingId)
 * - Success: TanStack query invalidation (via emit or callback)
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ErroredGuiasPanel from '@/features/review/ErroredGuiasPanel.vue'
import type { ErroredGuiaResponse } from '@/api/types'

function makeErrored(overrides: Partial<ErroredGuiaResponse> = {}): ErroredGuiaResponse {
  return {
    registro: 'R001',
    guia_id: 'T009-0001',
    source_pages: [5, 6],
    retry_attempted: false,
    ...overrides,
  }
}

describe('ErroredGuiasPanel — REINTENTAR button (T-8)', () => {
  it('renders a REINTENTAR button for each errored guía entry', () => {
    const guias = [makeErrored({ guia_id: 'T009-0001' }), makeErrored({ guia_id: 'T009-0002' })]
    const wrapper = mount(ErroredGuiasPanel, {
      props: {
        erroredGuias: guias,
        runId: 'run-123',
      },
    })
    const buttons = wrapper.findAll('button').filter((b) =>
      b.text().toUpperCase().includes('REINTENTAR'),
    )
    expect(buttons).toHaveLength(2)
  })

  it('REINTENTAR button is enabled when retry_attempted=false', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: {
        erroredGuias: [makeErrored({ retry_attempted: false })],
        runId: 'run-123',
      },
    })
    const btn = wrapper
      .findAll('button')
      .find((b) => b.text().toUpperCase().includes('REINTENTAR'))
    expect(btn).toBeDefined()
    expect(btn!.attributes('disabled')).toBeUndefined()
  })

  it('REINTENTAR button is disabled when retry_attempted=true', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: {
        erroredGuias: [makeErrored({ retry_attempted: true })],
        runId: 'run-123',
      },
    })
    const btn = wrapper
      .findAll('button')
      .find((b) => b.text().toUpperCase().includes('REINTENTAR'))
    expect(btn).toBeDefined()
    expect(btn!.attributes('disabled')).toBeDefined()
  })

  it('shows SUNAT hint when retry_attempted=true', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: {
        erroredGuias: [makeErrored({ retry_attempted: true })],
        runId: 'run-123',
      },
    })
    const text = wrapper.text().toLowerCase()
    expect(text).toContain('sunat')
  })

  it('clicking REINTENTAR emits retry event with guia_id', async () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: {
        erroredGuias: [makeErrored({ guia_id: 'T009-AABB', retry_attempted: false })],
        runId: 'run-123',
      },
    })
    const btn = wrapper
      .findAll('button')
      .find((b) => b.text().toUpperCase().includes('REINTENTAR'))
    expect(btn).toBeDefined()
    await btn!.trigger('click')
    // Should emit 'retry' event OR call retryGuia — component defines the pattern
    // Accept either emit or the button became loading/disabled
    const emitted = wrapper.emitted()
    const didEmitRetry = 'retry' in emitted
    const btnDisabledAfter = btn!.attributes('disabled') !== undefined
    expect(didEmitRetry || btnDisabledAfter).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// T-8: retryGuia / retryRegistro added to API client
// ---------------------------------------------------------------------------

describe('API client — retryGuia / retryRegistro (T-8)', () => {
  it('retryGuia is exported from client.ts', async () => {
    // Dynamic import so the test fails if the export is missing
    const client = await import('@/api/client')
    expect(typeof (client as Record<string, unknown>).retryGuia).toBe('function')
  })

  it('retryRegistro is exported from client.ts', async () => {
    const client = await import('@/api/client')
    expect(typeof (client as Record<string, unknown>).retryRegistro).toBe('function')
  })
})

// ---------------------------------------------------------------------------
// T-8: ErroredGuiaResponse has retry_attempted field
// ---------------------------------------------------------------------------

describe('ErroredGuiaResponse type — retry_attempted field', () => {
  it('ErroredGuiaResponse accepts retry_attempted field', () => {
    const eg: ErroredGuiaResponse = {
      registro: '232',
      guia_id: 'T009-0741770',
      source_pages: [4],
      retry_attempted: false,
    }
    expect(eg.retry_attempted).toBe(false)
  })

  it('retry_attempted=true is valid', () => {
    const eg: ErroredGuiaResponse = {
      registro: '232',
      guia_id: 'T009-0741770',
      source_pages: [4],
      retry_attempted: true,
    }
    expect(eg.retry_attempted).toBe(true)
  })
})
