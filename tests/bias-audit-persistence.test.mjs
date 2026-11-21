import assert from 'node:assert/strict'
import test, { after, before, beforeEach } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ configFile: 'vite.config.ts', appType: 'custom', logLevel: 'error', server: { middlewareMode: true } })
const auditApi = await vite.ssrLoadModule('/src/jobAuditClient.ts')
const sharedApi = await vite.ssrLoadModule('/src/apiClient.ts')
const expiry = await vite.ssrLoadModule('/src/sessionExpiry.ts')
after(async () => { await vite.close() })

let originalFetch
let originalDocument
before(() => { originalFetch = globalThis.fetch; originalDocument = globalThis.document })
after(() => { globalThis.fetch = originalFetch; globalThis.document = originalDocument })
beforeEach(() => { globalThis.fetch = originalFetch; globalThis.document = { cookie: 'skillfair_csrf=csrf-test' }; expiry.resetSessionExpiredNotification() })

const input = { job_title: 'Analyst', job_description: 'Role criteria.', requirements: [] }
const saved = {
  audit_id: 'a'.repeat(32), job_title: 'Analyst', created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
  criteria_reviewed: 1, finding_count: 1, reviewed_finding_count: 0,
  result: { job_title: 'Analyst', criteria_reviewed: 1, finding_count: 1, findings: [], human_review_required: true, disclaimer: 'Advisory only.' },
  review_selections: [{ finding_index: 0, review_state: 'needs_review', updated_at: '2026-11-01T00:00:00Z' }],
}

test('saved audit APIs use owner-scoped routes and only send audit input for server recomputation', async () => {
  const calls = []
  const fetcher = async (url, init) => {
    calls.push({ url, init })
    return new Response(JSON.stringify(url === '/api/jobs/audits?limit=20' ? { audits: [] } : saved), { status: 200 })
  }
  await auditApi.saveAudit(input, fetcher)
  await auditApi.listSavedAudits(fetcher)
  await auditApi.getSavedAudit(saved.audit_id, fetcher)
  await auditApi.updateSavedAuditReview(saved.audit_id, 0, 'accepted', fetcher)
  await auditApi.deleteSavedAudit(saved.audit_id, async (url, init) => {
    calls.push({ url, init })
    return new Response(null, { status: 204 })
  })
  assert.deepEqual(calls.map(({ url }) => url), [
    '/api/jobs/audits', '/api/jobs/audits?limit=20', `/api/jobs/audits/${saved.audit_id}`,
    `/api/jobs/audits/${saved.audit_id}/findings/0`, `/api/jobs/audits/${saved.audit_id}`,
  ])
  assert.equal(calls[0].init.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].init.body), input)
  assert.equal(calls[3].init.method, 'PATCH')
  assert.deepEqual(JSON.parse(calls[3].init.body), { review_state: 'accepted' })
})

test('saved audit writes retain shared cookies and CSRF protections', async () => {
  let call
  globalThis.fetch = async (url, init) => {
    call = { url, init }
    return new Response(JSON.stringify(saved), { status: 200 })
  }
  await auditApi.saveAudit(input)
  assert.equal(call.url, '/api/jobs/audits')
  assert.equal(call.init.credentials, 'include')
  assert.equal(new Headers(call.init.headers).get('X-CSRF-Token'), 'csrf-test')
})

test('saved audit failures stay safe and account changes reject late results', async () => {
  await assert.rejects(auditApi.getSavedAudit(saved.audit_id, async () => new Response('{"detail":"Saved audit was not found."}', { status: 404 })), /not found/)
  let respond
  globalThis.fetch = async () => new Promise((resolve) => { respond = resolve })
  const pending = auditApi.listSavedAudits()
  await new Promise((resolve) => setImmediate(resolve))
  expiry.resetSessionExpiredNotification()
  respond(new Response('{"audits":[]}', { status: 200 }))
  await assert.rejects(pending, (error) => error instanceof sharedApi.ApiSessionChangedError)
})
