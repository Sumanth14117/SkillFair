import assert from 'node:assert/strict'
import test, { after, beforeEach } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: 'vite.config.ts',
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
})
const market = await vite.ssrLoadModule('/src/marketIntelligenceClient.ts')
after(async () => { await vite.close() })

let originalFetch
let originalDocument
beforeEach(() => {
  originalFetch = globalThis.fetch
  originalDocument = globalThis.document
  globalThis.document = { cookie: 'skillfair_csrf=csrf-test' }
})
after(() => {
  globalThis.fetch = originalFetch
  if (originalDocument === undefined) delete globalThis.document
  else globalThis.document = originalDocument
})

const result = {
  job_title: 'Data Engineer',
  geography: 'Bengaluru, IN',
  data_availability: 'available',
  freshness: 'current',
  sources: [{
    source_id: 'approved-source', source_name: 'Approved source', source_type: 'job-market API',
    verification_status: 'verified', retrieved_at: '2026-11-06T10:00:00Z', published_at: null,
    geography: 'India', source_reference: 'https://source.example.test/report',
  }],
  insights: [{
    insight_id: 'demand-count', category: 'role_demand', title: 'Observed postings',
    summary: 'Provider-reported role posting count.', value: 25, unit: 'postings', period: '2026-11',
    geography: 'India', source_id: 'approved-source',
    calculation_method: 'Count returned by the provider for the submitted role and location.', data_quality: 'moderate',
  }],
  message: 'Attributed provider observation.',
}

test('market lookup uses the same-origin API and preserves provider provenance fields', async () => {
  let request
  globalThis.fetch = async (url, init) => {
    request = { url, init }
    return new Response(JSON.stringify(result), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  const data = await market.fetchMarketIntelligence({ job_title: 'Data Engineer', geography: 'Bengaluru, IN' })
  assert.equal(request.url, '/api/market/intelligence')
  assert.equal(request.init.credentials, 'include')
  assert.equal(request.init.headers.get('X-CSRF-Token'), 'csrf-test')
  assert.deepEqual(data, result)
  assert.equal(data.insights[0].calculation_method, result.insights[0].calculation_method)
  assert.equal(data.insights[0].data_quality, 'moderate')
})

test('market lookup represents unavailable data without manufacturing insights', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    job_title: 'Data Engineer', geography: null, data_availability: 'unavailable', freshness: 'not_available',
    sources: [], insights: [], message: 'No approved provider is configured.',
  }), { status: 200 })
  const data = await market.fetchMarketIntelligence({ job_title: 'Data Engineer' })
  assert.equal(data.data_availability, 'unavailable')
  assert.equal(data.freshness, 'not_available')
  assert.deepEqual(data.insights, [])
})

test('market lookup rejects a response that omits freshness metadata', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ ...result, freshness: undefined }), { status: 200 })
  await assert.rejects(
    market.fetchMarketIntelligence({ job_title: 'Data Engineer' }),
    /unsupported response/,
  )
})

test('provider status displays availability without requiring credentials in the response', async () => {
  let request
  globalThis.fetch = async (url, init) => {
    request = { url, init }
    return new Response(JSON.stringify({ provider: null, availability: 'not_configured', checked_at: null, message: 'No approved provider is configured.' }), { status: 200 })
  }
  const status = await market.fetchProviderStatus()
  assert.equal(request.url, '/api/market/provider-status')
  assert.equal(request.init.method, 'GET')
  assert.deepEqual(status, { provider: null, availability: 'not_configured', checked_at: null, message: 'No approved provider is configured.' })
})
