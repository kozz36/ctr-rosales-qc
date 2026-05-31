/**
 * REVIEW-ONLY API MOCK (dev infrastructure — NOT production code).
 *
 * Activated solely when VITE_MOCK=1. Intercepts the shared axios instance
 * exported by @/api/client and serves deterministic fixtures that force every
 * visual state of the reconciliation review UI:
 *   MATCH, MISMATCH, DECLARED_MISSING, GUIA_MISSING, UNCLASSIFIED
 *   confidence < 0.85 (flagged) and >= 0.85 (ok) and null (trusted/digital)
 *   multiple (registro,fecha) groups, multi-page source provenance,
 *   Decimal fields serialised as STRINGS (matching backend behaviour),
 *   run status sequence processing -> review, and an audit trail.
 *
 * This module does NOT modify any reviewed component. It only attaches an
 * axios-mock-adapter to the existing http instance at startup.
 */
import MockAdapter from 'axios-mock-adapter'
import { http } from '@/api/client'

const MOCK_RUN_ID = '7c9e6f12-3a4b-4c5d-8e9f-0a1b2c3d4e5f'

// Decimal fields are STRINGS on purpose (backend serialises Decimal -> str).
const ROWS = [
  // Group 1: Registro 4251 / 2024-03-15 — MATCH + MISMATCH + low confidence
  {
    row_id: '4251|2024-03-15|FIERRO CORRUGADO 1/2"|KG',
    registro: '4251',
    fecha: '2024-03-15',
    material_canonical: 'FIERRO CORRUGADO 1/2"',
    unidad: 'KG',
    declared_qty: '12500.00',
    summed_qty: '12500.00',
    delta: '0.00',
    status: 'MATCH',
    source_pages: [12, 13],
    min_confidence: 0.97,
  },
  {
    row_id: '4251|2024-03-15|CEMENTO PORTLAND TIPO I|RD',
    registro: '4251',
    fecha: '2024-03-15',
    material_canonical: 'CEMENTO PORTLAND TIPO I',
    unidad: 'RD',
    declared_qty: '850.00',
    summed_qty: '845.00',
    delta: '-5.00',
    status: 'MISMATCH',
    source_pages: [14, 15, 16],
    min_confidence: 0.72, // FLAGGED (< 0.85)
  },
  {
    row_id: '4251|2024-03-15|ALAMBRE NEGRO N16|RD',
    registro: '4251',
    fecha: '2024-03-15',
    material_canonical: 'ALAMBRE NEGRO N16',
    unidad: 'RD',
    declared_qty: '120.00',
    summed_qty: '120.00',
    delta: '0.00',
    status: 'MATCH',
    source_pages: [17],
    min_confidence: null, // trusted / digital
  },
  // Group 2: Registro 4252 / 2024-03-18 — DECLARED_MISSING + GUIA_MISSING
  {
    row_id: '4252|2024-03-18|TUBERIA PVC 4"|Rollo',
    registro: '4252',
    fecha: '2024-03-18',
    material_canonical: 'TUBERIA PVC 4"',
    unidad: 'Rollo',
    declared_qty: '0.00',
    summed_qty: '36.00',
    delta: '+36.00',
    status: 'DECLARED_MISSING',
    source_pages: [22],
    min_confidence: 0.81, // FLAGGED (< 0.85)
  },
  {
    row_id: '4252|2024-03-18|MALLA ELECTROSOLDADA Q188|TN',
    registro: '4252',
    fecha: '2024-03-18',
    material_canonical: 'MALLA ELECTROSOLDADA Q188',
    unidad: 'TN',
    declared_qty: '4.5000',
    summed_qty: '0.00',
    delta: '-4.5000',
    status: 'GUIA_MISSING',
    source_pages: [],
    min_confidence: null,
  },
  // Group 3: Registro 4260 / null fecha — UNCLASSIFIED + a big MISMATCH
  {
    row_id: '4260|None|ARENA GRUESA|TN',
    registro: '4260',
    fecha: null,
    material_canonical: 'ARENA GRUESA',
    unidad: 'TN',
    declared_qty: '210.00',
    summed_qty: '198.50',
    delta: '-11.50',
    status: 'MISMATCH',
    source_pages: [31, 32, 33, 34],
    min_confidence: 0.66, // FLAGGED
  },
  {
    row_id: '4260|None|PIEDRA CHANCADA 3/4"|TN',
    registro: '4260',
    fecha: null,
    material_canonical: 'PIEDRA CHANCADA 3/4"',
    unidad: 'TN',
    declared_qty: '95.00',
    summed_qty: '95.00',
    delta: '0.00',
    status: 'UNCLASSIFIED',
    source_pages: [35],
    min_confidence: 0.9,
  },
]

const AUDIT = {
  run_id: MOCK_RUN_ID,
  events: [
    {
      timestamp: '2024-03-20T14:02:11+00:00',
      kind: 'field_edit',
      target: { guia_id: 'G-0007' },
      field: 'fecha',
      old_value: '2024-03-14',
      new_value: '2024-03-15',
    },
    {
      timestamp: '2024-03-20T14:05:43+00:00',
      kind: 'reassignment',
      target: { guia_id: 'G-0012' },
      field: null,
      old_value: { registro: '4259', fecha: '2024-03-18' },
      new_value: { registro: '4252', fecha: '2024-03-18' },
    },
  ],
}

export function installReviewMock(): void {
  const mock = new MockAdapter(http, { delayResponse: 250 })

  // Status sequence: first 2 polls processing, then review.
  let statusCalls = 0
  mock.onGet(new RegExp(`/runs/${MOCK_RUN_ID}$`)).reply(() => {
    statusCalls += 1
    const status = statusCalls >= 3 ? 'review' : 'processing'
    return [
      200,
      {
        run_id: MOCK_RUN_ID,
        status,
        vision_calls_made: status === 'review' ? 42 : 18,
        warnings:
          status === 'review'
            ? ['3 guías con confianza por debajo del umbral 85% — revisar.']
            : [],
        error: null,
      },
    ]
  })

  mock.onGet(new RegExp(`/runs/${MOCK_RUN_ID}/table$`)).reply(200, {
    run_id: MOCK_RUN_ID,
    rows: ROWS,
  })

  mock.onGet(new RegExp(`/runs/${MOCK_RUN_ID}/audit$`)).reply(200, AUDIT)

  // Upload -> returns the fixed run id.
  mock.onPost(/\/runs$/).reply(202, { run_id: MOCK_RUN_ID, status: 'pending' })

  // Edit / reassign just echo back the rows (and surface the contract issue:
  // backend would 422 on a row_id sent as guia_id; here we accept to render UI).
  mock.onPatch(new RegExp(`/runs/${MOCK_RUN_ID}/rows/.+`)).reply((config) => {
    // eslint-disable-next-line no-console
    console.warn('[review-mock] PATCH body:', config.data)
    return [200, { run_id: MOCK_RUN_ID, rows: ROWS }]
  })
  mock.onPost(new RegExp(`/runs/${MOCK_RUN_ID}/reassign$`)).reply((config) => {
    // eslint-disable-next-line no-console
    console.warn('[review-mock] REASSIGN body:', config.data)
    return [200, { run_id: MOCK_RUN_ID, rows: ROWS }]
  })

  mock.onPost(new RegExp(`/runs/${MOCK_RUN_ID}/export$`)).reply(
    200,
    new Blob(['mock,csv,content\n1,2,3'], { type: 'text/csv' }),
  )

  // Thumbnails: 404 so SourcePages degrades to number chips (real behaviour).
  mock.onGet(/\/pages\/\d+\/thumbnail$/).reply(404)

  // eslint-disable-next-line no-console
  console.info(`[review-mock] active — open /runs/${MOCK_RUN_ID} for the review grid`)
}

export { MOCK_RUN_ID }
