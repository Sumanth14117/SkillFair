import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: 'vite.config.ts',
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
})
const { loadWorkflowHistory } = await vite.ssrLoadModule('/src/workflowHistory.ts')
const { default: WorkflowHistory } = await vite.ssrLoadModule('/src/components/WorkflowHistory.tsx')
const { restoreSavedWorkflow } = await vite.ssrLoadModule('/src/workflowRestoration.ts')
after(async () => { await vite.close() })

const olderRunId = '550e8400e29b41d4a716446655440000'
const newerRunId = '550e8400e29b41d4a716446655440001'
const createdAt = '2026-10-30T09:00:00Z'

function listItem(runId, jobTitle, status = 'awaiting_human_review') {
  return {
    run_id: runId,
    job_title: jobTitle,
    workflow_status: status,
    review_decision: status === 'complete' ? 'proceed' : null,
    created_at: createdAt,
    updated_at: createdAt,
  }
}

function workflow(runId) {
  return {
    run_id: runId,
    started_at: createdAt,
    workflow_status: 'awaiting_human_review',
    stages: [
      { stage: 'human_review', status: 'needs_human_review', summary: 'Review required.' },
      { stage: 'learning_pathways', status: 'blocked_pending_review', summary: 'Blocked pending review.' },
    ],
    skill_passport: {}, market_intelligence: {}, job_audit: {}, inclusive_matching: {},
    human_review: { status: 'pending', decision: null, reviewable_requirement_ids: [], reviewed_requirement_ids: [], review_notes: null, attested_at: null, reviewer_identity_verified: false },
    learning_pathways: { status: 'blocked_pending_review', message: 'Review required.', result: null },
    workflow_summary: 'Awaiting review.', disclaimers: [],
    workflow_inputs: { job_title: 'Data Analyst', job_description: 'Use Python for reporting.', geography: null, essential_skills: ['Python'], preferred_skills: ['SQL'] },
  }
}

function render(state, action = null) {
  return renderToStaticMarkup(React.createElement(WorkflowHistory, { state, action, onRefresh() {}, onContinue() {} }))
}

test('history panel renders loading, empty and retryable error states', () => {
  assert.match(render({ status: 'loading' }), /Loading saved workflows/)
  assert.match(render({ status: 'ready', items: [] }), /No saved workflows yet/)
  const failed = render({ status: 'error', message: 'History service unavailable.' })
  assert.match(failed, /role="alert"/)
  assert.match(failed, /History service unavailable/)
  assert.match(failed, /Retry/)
})

test('history request uses authenticated list endpoint and preserves backend newest-first order', async () => {
  const calls = []
  const result = await loadWorkflowHistory('account-a', () => 'account-a', async (url) => {
    calls.push(url)
    return new Response(JSON.stringify([
      listItem(newerRunId, 'Newer Analyst'),
      listItem(olderRunId, 'Older Analyst', 'complete'),
    ]), { status: 200 })
  })
  assert.deepEqual(calls, ['/api/career/orchestrator/runs?limit=20'])
  assert.equal(result.status, 'ready')
  assert.deepEqual(result.items.map((item) => item.run_id), [newerRunId, olderRunId])
  assert.equal(result.items[1].workflow_status, 'complete')
  assert.equal(result.items[1].review_decision, 'proceed')
  assert.equal('job_description' in result.items[1], false)
  assert.equal('workflow' in result.items[1], false)
})

test('empty and unavailable API responses become explicit states without fabricated records', async () => {
  const empty = await loadWorkflowHistory('account-a', () => 'account-a', async () => new Response('[]', { status: 200 }))
  assert.deepEqual(empty, { status: 'ready', items: [] })
  await assert.rejects(
    loadWorkflowHistory('account-a', () => 'account-a', async () => new Response('{}', { status: 503 })),
    /Saved workflows could not be loaded/,
  )
})

test('switching account while the history body is pending discards the old account response', async () => {
  let currentOwner = 'account-a'
  let finishBody
  const pending = loadWorkflowHistory('account-a', () => currentOwner, async () => ({
    ok: true,
    status: 200,
    json: () => new Promise((resolve) => { finishBody = resolve }),
  }))
  await new Promise((resolve) => setImmediate(resolve))
  currentOwner = 'account-b'
  finishBody([listItem(olderRunId, 'Account A private title')])
  assert.equal(await pending, null)
})

test('continuing a history entry restores its exact ID and never requests a latest-workflow fallback', async () => {
  const calls = []
  const result = await restoreSavedWorkflow(olderRunId, async (url) => {
    calls.push(url)
    return new Response(JSON.stringify(workflow(olderRunId)), { status: 200 })
  })
  assert.deepEqual(calls, [`/api/career/orchestrator/runs/${olderRunId}`])
  assert.equal(result.kind, 'restored')
  assert.equal(result.run.run_id, olderRunId)
  assert.equal(result.workflowInputs.job_description, 'Use Python for reporting.')
  assert.notEqual(result.run.run_id, newerRunId)
})

test('history card shows persisted workflow status, timestamps, ID, and continue action', () => {
  const item = { ...listItem(olderRunId, 'Data Analyst', 'revise_required'), workflow_status: 'revise_required', review_decision: 'revise' }
  const html = render({ status: 'ready', items: [item] })
  assert.match(html, /Data Analyst/)
  assert.match(html, /Revised criteria needed/)
  assert.match(html, /Human review decision · revise/)
  assert.match(html, /Created/)
  assert.match(html, new RegExp(olderRunId))
  assert.match(html, /Continue Workflow/)
})
