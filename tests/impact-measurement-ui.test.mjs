import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: 'vite.config.ts',
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
})
const { default: ImpactMeasurement } = await vite.ssrLoadModule('/src/components/ImpactMeasurement.tsx')
const { getDurationTrendEmptyState } = await vite.ssrLoadModule('/src/impactMeasurementChart.ts')
const { AuthContext } = await vite.ssrLoadModule('/src/authContext.ts')
after(async () => { await vite.close() })

test('impact dashboard presents account-linked candidate feedback with explicit privacy disclosure', () => {
  const auth = {
    user: { id: 'test-account-a', email: 'reviewer@example.test', created_at: '2026-01-01T00:00:00Z' },
    status: 'authenticated', error: null,
    login: async () => {}, register: async () => {}, logout: async () => {}, retryCurrentUser: () => {},
  }
  const html = renderToStaticMarkup(createElement(AuthContext.Provider, { value: auth }, createElement(ImpactMeasurement)))
  assert.match(html, /Loading aggregate measurements/)
  assert.match(html, /account-scoped pilot measurements/i)
  assert.match(html, /associated with your signed-in account/i)
  assert.match(html, /candidate feedback is not anonymous/i)
  assert.match(html, /does not independently verify that a respondent is a candidate/i)
  assert.match(html, /No free-text comments are collected/i)
  assert.match(html, /How clear were the job requirements/i)
  assert.match(html, /I voluntarily agree that these ratings and selected themes may be linked/i)
  assert.doesNotMatch(html, /Jordan Lee|Samira Khan|Marcus Reed|95%|1,284/)
})

test('duration chart reports its largest bucket separately from the overall KPI sample', () => {
  const points = [1, 1, 1, 1, 1].map((sample_size, index) => ({
    bucket_start: `2026-10-31T10:${String(index * 5).padStart(2, '0')}:00Z`,
    average_seconds: null,
    sample_size,
    data_availability: 'insufficient_sample',
  }))

  assert.deepEqual(getDurationTrendEmptyState(points, 5, 'insufficient_sample'), {
    heading: 'Insufficient sample per time bucket',
    detail: 'Largest 5-minute bucket · 1 of 5 required; valid observations across selected period · 5. No fabricated chart values are shown.',
  })
})
