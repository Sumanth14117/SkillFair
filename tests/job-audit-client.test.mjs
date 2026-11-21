import assert from 'node:assert/strict'
import test, { after, before, beforeEach } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ configFile: 'vite.config.ts', appType: 'custom', logLevel: 'error', server: { middlewareMode: true } })
const jobAudit = await vite.ssrLoadModule('/src/jobAuditClient.ts')
const api = await vite.ssrLoadModule('/src/apiClient.ts')
const expiry = await vite.ssrLoadModule('/src/sessionExpiry.ts')
after(async () => { await vite.close() })

let originalFetch
let originalDocument
before(() => { originalFetch = globalThis.fetch })
before(() => { originalDocument = globalThis.document })
after(() => { globalThis.fetch = originalFetch; globalThis.document = originalDocument })
beforeEach(() => { globalThis.fetch = originalFetch; globalThis.document = { cookie: 'skillfair_csrf=csrf-test-token' }; expiry.resetSessionExpiredNotification() })

const result = { job_title: 'Analyst', criteria_reviewed: 1, finding_count: 0, findings: [], human_review_required: true, disclaimer: 'Advisory only.' }
const payload = { job_title: 'Analyst', job_description: 'Role text.', requirements: [] }

test('Job Audit submits through the shared client and validates successful results', async () => {
  let call
  const audit = await jobAudit.submitAudit(payload, async (url, init) => {
    call = { url, init }
    return new Response(JSON.stringify(result), { status: 200 })
  })
  assert.equal(call.url, '/api/jobs/audit')
  assert.equal(call.init.method, 'POST')
  assert.equal(JSON.parse(call.init.body).job_description, 'Role text.')
  assert.equal(audit.finding_count, 0)
})

test('Job Audit reports authentication failure without retrying', async () => {
  let calls = 0
  let expirations = 0
  const unregister = expiry.registerSessionExpiredHandler(() => { expirations += 1 })
  globalThis.fetch = async (_url, init) => {
    calls += 1
    assert.equal(init.credentials, 'include')
    assert.equal(new Headers(init.headers).get('X-CSRF-Token'), 'csrf-test-token')
    return new Response('{}', { status: 401 })
  }
  try {
    await assert.rejects(jobAudit.submitAudit(payload), /sign-in session has expired/)
    assert.equal(expirations, 1)
  } finally { unregister() }
  assert.equal(calls, 1)
})

test('Job Audit reports server failures without retrying', async () => {
  let calls = 0
  await assert.rejects(jobAudit.submitAudit(payload, async () => { calls += 1; return new Response('{"detail":"Unavailable"}', { status: 503 }) }), /Unavailable/)
  assert.equal(calls, 1)
})

test('Job Audit preserves shared timeout errors', async () => {
  await assert.rejects(jobAudit.submitAudit(payload, async () => { throw new api.ApiRequestTimeoutError() }), (error) => error instanceof api.ApiRequestTimeoutError)
})

test('Job Audit discards a late response after the account session changes', async () => {
  let respond
  globalThis.fetch = async () => new Promise((resolve) => { respond = resolve })
  const pending = jobAudit.submitAudit(payload)
  await new Promise((resolve) => setImmediate(resolve))
  expiry.resetSessionExpiredNotification()
  respond(new Response(JSON.stringify(result), { status: 200 }))
  await assert.rejects(pending, (error) => error instanceof api.ApiSessionChangedError)
})
