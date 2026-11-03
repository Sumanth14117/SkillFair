import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: 'vite.config.ts',
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
})
const { restoreSavedWorkflow } = await vite.ssrLoadModule('/src/workflowRestoration.ts')
after(async () => { await vite.close() })

const olderRunId = '550e8400e29b41d4a716446655440000'
const newerRunId = '550e8400e29b41d4a716446655440001'

function pendingWorkflow(runId) {
  return {
    run_id: runId,
    started_at: '2026-10-31T00:00:00Z',
    workflow_status: 'awaiting_human_review',
    stages: [
      { stage: 'human_review', status: 'pending', summary: 'Review required.' },
      { stage: 'learning_pathways', status: 'blocked', summary: 'Blocked pending review.' },
    ],
    skill_passport: {}, market_intelligence: {}, job_audit: {}, inclusive_matching: {},
    human_review: { status: 'pending', decision: null, reviewable_requirement_ids: [], reviewed_requirement_ids: [], review_notes: null, attested_at: null, reviewer_identity_verified: false },
    learning_pathways: { status: 'blocked_pending_review', message: 'Review required.', result: null },
    workflow_summary: 'Awaiting review.', disclaimers: [],
  }
}

test('refresh restoration retrieves the saved older workflow by its exact ID', async () => {
  const calls = []
  const result = await restoreSavedWorkflow(olderRunId, async (url) => {
    calls.push(url)
    return new Response(JSON.stringify(pendingWorkflow(olderRunId)), { status: 200 })
  })
  assert.deepEqual(calls, [`/api/career/orchestrator/runs/${olderRunId}`])
  assert.equal(result.kind, 'restored')
  assert.equal(result.run.run_id, olderRunId)
  assert.notEqual(result.run.run_id, newerRunId)
})

test('missing, inaccessible, and malformed saved IDs never fall back to a newer workflow', async () => {
  let calls = 0
  const missing = await restoreSavedWorkflow(olderRunId, async () => {
    calls += 1
    return new Response('{"detail":"not found"}', { status: 404 })
  })
  assert.deepEqual(missing, { kind: 'missing' })
  const invalid = await restoreSavedWorkflow('bad-id', async () => {
    calls += 1
    throw new Error('An invalid ID must not make a request')
  })
  assert.deepEqual(invalid, { kind: 'missing' })
  assert.equal(calls, 1)
})

test('restoration rejects a mismatched backend run ID and reports expired sessions', async () => {
  await assert.rejects(
    restoreSavedWorkflow(olderRunId, async () => new Response(JSON.stringify(pendingWorkflow(newerRunId)), { status: 200 })),
    /mismatched workflow reference/,
  )
  await assert.rejects(
    restoreSavedWorkflow(olderRunId, async () => new Response('{"detail":"Authentication required."}', { status: 401 })),
    /session has expired/,
  )
})

test('refresh cleanup can cancel an in-flight workflow restoration request', async () => {
  const controller = new AbortController()
  let receivedSignal
  const pending = restoreSavedWorkflow(olderRunId, async (_url, options) => {
    receivedSignal = options.signal
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Canceled', 'AbortError')), { once: true })
    })
  }, controller.signal)
  controller.abort()
  assert.equal(receivedSignal, controller.signal)
  await assert.rejects(pending, (error) => error.name === 'AbortError')
})
