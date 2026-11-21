import assert from 'node:assert/strict'
import test, { after, before, beforeEach } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ configFile: 'vite.config.ts', appType: 'custom', logLevel: 'error', server: { middlewareMode: true } })
const feedback = await vite.ssrLoadModule('/src/candidateFeedbackClient.ts')
const sharedApi = await vite.ssrLoadModule('/src/apiClient.ts')
const expiry = await vite.ssrLoadModule('/src/sessionExpiry.ts')
after(async () => { await vite.close() })

let originalFetch
let originalDocument
before(() => { originalFetch = globalThis.fetch; originalDocument = globalThis.document })
after(() => { globalThis.fetch = originalFetch; globalThis.document = originalDocument })
beforeEach(() => { globalThis.fetch = originalFetch; globalThis.document = { cookie: 'skillfair_csrf=csrf-feedback' }; expiry.resetSessionExpiredNotification() })

const payload = {
  submission_id: '00000000-0000-4000-8000-000000000001', consent_to_aggregate: true,
  application_experience: 4, process_accessibility: 3, job_requirements_clarity: 5, perceived_fairness: 2,
  suggestion_categories: ['clarity', 'accessibility'],
}

test('candidate feedback submits structured ratings and controlled suggestions without comments', async () => {
  let call
  const result = await feedback.submitCandidateFeedback(payload, async (url, init) => {
    call = { url, init }
    return new Response(JSON.stringify({ accepted: true, duplicate: false, message: 'Recorded with account.' }), { status: 200 })
  })
  assert.equal(call.url, '/api/impact/feedback/candidate')
  assert.equal(call.init.method, 'POST')
  assert.deepEqual(JSON.parse(call.init.body), payload)
  assert.equal('comment' in JSON.parse(call.init.body), false)
  assert.equal(result.accepted, true)
})

test('candidate feedback uses shared authenticated CSRF request and does not retry', async () => {
  let calls = 0
  globalThis.fetch = async (_url, init) => {
    calls += 1
    assert.equal(init.credentials, 'include')
    assert.equal(new Headers(init.headers).get('X-CSRF-Token'), 'csrf-feedback')
    return new Response(JSON.stringify({ accepted: true, duplicate: false, message: 'Recorded.' }), { status: 200 })
  }
  await feedback.submitCandidateFeedback(payload)
  assert.equal(calls, 1)
})

test('candidate feedback reports validation, authentication, and stalled-account responses safely', async () => {
  await assert.rejects(feedback.submitCandidateFeedback(payload, async () => new Response('{}', { status: 422 })), /Check the ratings/)
  await assert.rejects(feedback.submitCandidateFeedback(payload, async () => new Response('{}', { status: 401 })), /session has expired/)
  let respond
  globalThis.fetch = async () => new Promise((resolve) => { respond = resolve })
  const pending = feedback.submitCandidateFeedback(payload)
  await new Promise((resolve) => setImmediate(resolve))
  expiry.resetSessionExpiredNotification()
  respond(new Response(JSON.stringify({ accepted: true, duplicate: false, message: 'Late' }), { status: 200 }))
  await assert.rejects(pending, (error) => error instanceof sharedApi.ApiSessionChangedError)
})
