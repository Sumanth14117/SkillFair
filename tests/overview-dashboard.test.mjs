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
const { loadDashboardOverview, dashboardStateForOwner } = await vite.ssrLoadModule('/src/overviewDashboard.ts')
const { default: DashboardView } = await vite.ssrLoadModule('/src/components/OverviewDashboardView.tsx')
after(async () => { await vite.close() })

const workflowId = '550e8400e29b41d4a716446655440000'
const overview = {
  generated_at: '2026-11-06T10:00:00Z',
  summary: { saved_analyses: 2, total_workflows: 3, pending_reviews: 1, completed_reviews: 2 },
  workflow_status_distribution: [
    { status: 'awaiting_human_review', count: 1 },
    { status: 'complete', count: 1 },
    { status: 'stopped', count: 1 },
  ],
  review_decision_distribution: [
    { decision: 'pending', count: 1 },
    { decision: 'proceed', count: 1 },
    { decision: 'stop', count: 1 },
  ],
  recent_workflows: [{
    run_id: workflowId, job_title: 'Data Analyst', workflow_status: 'awaiting_human_review',
    review_decision: 'pending', created_at: '2026-11-05T10:00:00Z', updated_at: '2026-11-05T10:00:00Z',
  }],
  recent_activity: [{
    event_type: 'workflow_created', workflow_id: workflowId, job_title: 'Data Analyst',
    occurred_at: '2026-11-05T10:00:00Z', workflow_status: 'awaiting_human_review', decision: null,
    description: 'Workflow created for Data Analyst.',
  }],
}

function render(state) {
  return renderToStaticMarkup(React.createElement(DashboardView, {
    state, greetingName: 'Sumanth', onRetry() {}, onDiscoverSkills() {}, onViewWorkflows() {},
  }))
}

test('dashboard view presents a loading state without placeholder numbers', () => {
  const html = render({ status: 'loading', ownerId: 'account-a' })
  assert.match(html, /Loading your account overview/)
  assert.doesNotMatch(html, /1,284|342|Jordan Lee|Illustrative demonstration/)
})

test('dashboard renders actual summary, status distributions, activity and workflows', () => {
  const html = render({ status: 'ready', ownerId: 'account-a', data: overview })
  assert.match(html, /Saved analyses/)
  assert.match(html, />2</)
  assert.match(html, />3</)
  assert.match(html, /Awaiting human review/)
  assert.match(html, /Workflow created for Data Analyst/)
  assert.match(html, /Data Analyst/)
  assert.doesNotMatch(html, /Potential matches|Candidates reviewed|Sample candidate|Jordan Lee/)
})

test('dashboard empty response shows a clear empty workflow state and actual zeroes', () => {
  const empty = {
    ...overview,
    summary: { saved_analyses: 0, total_workflows: 0, pending_reviews: 0, completed_reviews: 0 },
    workflow_status_distribution: [], review_decision_distribution: [], recent_workflows: [], recent_activity: [],
  }
  const html = render({ status: 'ready', ownerId: 'account-a', data: empty })
  assert.match(html, /No saved workflows yet/)
  assert.match(html, /Discover skills/)
  assert.match(html, /No saved analyses or workflows/)
})

test('dashboard API uses the authenticated same-origin endpoint and validates its response', async () => {
  const calls = []
  const data = await loadDashboardOverview('account-a', () => 'account-a', async (url, init) => {
    calls.push({ url, init })
    return new Response(JSON.stringify(overview), { status: 200 })
  })
  assert.equal(calls[0].url, '/api/dashboard/overview')
  assert.equal(calls[0].init.method, 'GET')
  assert.deepEqual(data, overview)
  await assert.rejects(
    loadDashboardOverview('account-a', () => 'account-a', async () => new Response('{"summary":{}}', { status: 200 })),
    /unsupported format/,
  )
})

test('dashboard displays a safe retry state for API failures', async () => {
  await assert.rejects(
    loadDashboardOverview('account-a', () => 'account-a', async () => new Response('{"detail":"internal details"}', { status: 503 })),
    /temporarily unavailable/,
  )
  const html = render({ status: 'error', ownerId: 'account-a', message: 'Overview data is temporarily unavailable. Please retry.' })
  assert.match(html, /role="alert"/)
  assert.match(html, /Retry/)
  assert.doesNotMatch(html, /internal details/)
})

test('dashboard discards a response when the authenticated account changes during body reading', async () => {
  let currentOwner = 'account-a'
  let finishBody
  const pending = loadDashboardOverview('account-a', () => currentOwner, async () => ({
    ok: true, status: 200, json: () => new Promise((resolve) => { finishBody = resolve }),
  }))
  await new Promise((resolve) => setImmediate(resolve))
  currentOwner = 'account-b'
  finishBody(overview)
  assert.equal(await pending, null)
  assert.deepEqual(dashboardStateForOwner({ status: 'ready', ownerId: 'account-a', data: overview }, 'account-b'), {
    status: 'loading', ownerId: 'account-b',
  })
})
