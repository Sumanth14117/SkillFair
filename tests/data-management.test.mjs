import test from 'node:test'
import assert from 'node:assert/strict'
import { after } from 'node:test'
import { createServer } from 'vite'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const vite = await createServer({ configFile: 'vite.config.ts', appType: 'custom', logLevel: 'error', server: { middlewareMode: true } })
const accountData = await vite.ssrLoadModule('/src/accountData.ts')
const dataManagementUi = await vite.ssrLoadModule('/src/components/DataManagement.tsx')
after(async () => { await vite.close() })

const empty = { analysis_total: 0, workflow_total: 0, limit: 25, offset: 0, analyses: [], workflows: [] }

test('account data exposes loading and empty states only for the matching account', () => {
  assert.deepEqual(accountData.accountDataLoading('user-a'), { ownerId: 'user-a', status: 'loading' })
  const ready = accountData.accountDataReady('user-a', empty)
  assert.deepEqual(accountData.visibleAccountData(ready, 'user-a'), empty)
  assert.equal(accountData.visibleAccountData(ready, 'user-b'), null)
})

test('data management UI renders loading, empty, and retryable error states', () => {
  const render = (state, notice = null) => renderToStaticMarkup(React.createElement(dataManagementUi.DataManagementView, {
    ownerId: 'user-a', state, offset: 0, busy: null, notice,
    onExport() {}, onRetry() {}, onDelete() {}, onPage() {},
  }))
  assert.match(render(accountData.accountDataLoading('user-a')), /Loading your saved account data/)
  const emptyState = render(accountData.accountDataReady('user-a', empty))
  assert.match(emptyState, /No saved analyses are associated with this account/)
  assert.match(emptyState, /No saved workflows are associated with this account/)
  assert.match(render(accountData.accountDataError('user-a', 'Retry safely.'), { kind: 'error', text: 'Retry safely.' }), /Retry safely/)
  assert.match(render(accountData.accountDataReady('user-a', empty), { kind: 'success', text: 'The workflow was deleted.' }), /The workflow was deleted/)
})

test('account data loading validates the response and reports a safe API error', async () => {
  const controller = new AbortController()
  const data = await accountData.loadAccountData('user-a', 0, controller.signal, async (url) => {
    assert.equal(url, '/api/account/data?limit=25&offset=0')
    return new Response(JSON.stringify(empty), { status: 200 })
  })
  assert.equal(data.status, 'ready')
  await assert.rejects(accountData.loadAccountData('user-a', 0, controller.signal, async () => new Response(JSON.stringify({ detail: 'Account data is temporarily unavailable.' }), { status: 503 })), /temporarily unavailable/)
})

test('export downloads only the successful JSON response as a named file', async () => {
  const result = await accountData.downloadAccountExport(async (url) => {
    assert.equal(url, '/api/account/export')
    return new Response('{"format_version":"1.0"}', { status: 200 })
  })
  assert.equal(result.filename, 'skillfair-account-export.json')
  assert.equal(await result.blob.text(), '{"format_version":"1.0"}')
  await assert.rejects(accountData.downloadAccountExport(async () => new Response('{"detail":"Export is unavailable."}', { status: 503 })), /unavailable/)
})

test('delete confirmation prevents requests when cancelled and sends DELETE when confirmed', async () => {
  let requests = 0
  const request = async (url, init) => {
    requests += 1
    assert.equal(url, '/api/account/workflows/workflow-1234567890123456')
    assert.equal(init.method, 'DELETE')
    return new Response(null, { status: 204 })
  }
  const cancelled = await accountData.confirmAndDeleteAccountRecord('workflow', 'workflow-1234567890123456', () => false, request)
  assert.equal(cancelled, false)
  assert.equal(requests, 0)
  const deleted = await accountData.confirmAndDeleteAccountRecord('workflow', 'workflow-1234567890123456', (message) => message.includes('cannot be undone'), request)
  assert.equal(deleted, true)
  assert.equal(requests, 1)
})

test('analysis deletion reports dependency conflicts without hiding them', async () => {
  await assert.rejects(
    accountData.confirmAndDeleteAccountRecord('analysis', 'analysis-1234567890123456', () => true, async (_url, init) => {
      assert.equal(init.method, 'DELETE')
      return new Response(JSON.stringify({ detail: 'Delete the workflows linked to this analysis before deleting the analysis.' }), { status: 409 })
    }),
    /workflows linked/,
  )
})

