import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const vite = await createServer({ configFile: 'vite.config.ts', appType: 'custom', logLevel: 'error', server: { middlewareMode: true } })
const { searchAccount, listNotifications, markNotificationRead } = await vite.ssrLoadModule('/src/navigationClient.ts')
const { readUserPreferences, saveDisplayNamePreference } = await vite.ssrLoadModule('/src/userPreferences.ts')
const { GlobalSearch, NotificationsMenu } = await vite.ssrLoadModule('/src/components/NavigationUtilities.tsx')
const { default: SettingsPage } = await vite.ssrLoadModule('/src/components/SettingsPage.tsx')
const { default: HelpCenter } = await vite.ssrLoadModule('/src/components/HelpCenter.tsx')
after(async () => { await vite.close() })

test('saved-record search uses the shared authenticated API route and carries no resume input', async () => {
  const originalFetch = globalThis.fetch
  let requested = ''
  globalThis.fetch = async (url) => {
    requested = String(url)
    return Response.json({ query: 'Python', results: [] })
  }
  try {
    const response = await searchAccount('Python')
    assert.equal(requested, '/api/search?q=Python')
    assert.deepEqual(response.results, [])
  } finally { globalThis.fetch = originalFetch }
})

test('notification listing and read action use authenticated endpoints and CSRF-protected POST', async () => {
  const originalFetch = globalThis.fetch
  const originalDocument = globalThis.document
  const calls = []
  globalThis.document = { cookie: 'skillfair_csrf=test-csrf' }
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method ?? 'GET', csrf: new Headers(init.headers).get('X-CSRF-Token') })
    return Response.json({ notification_id: 'a'.repeat(32), notification_type: 'workflow_decision', title: 'Review saved', message: 'Decision saved.', created_at: '2026-11-19T00:00:00Z', read_at: null, workflow_id: 'b'.repeat(32) })
  }
  try {
    await listNotifications()
    await markNotificationRead('a'.repeat(32))
    assert.deepEqual(calls.map((call) => [call.url, call.method]), [
      ['/api/notifications?limit=20', 'GET'],
      [`/api/notifications/${'a'.repeat(32)}/read`, 'POST'],
    ])
    assert.equal(calls[1].csrf, 'test-csrf')
  } finally { globalThis.fetch = originalFetch; globalThis.document = originalDocument }
})

test('search and notification controls render accessible actions', () => {
  const html = renderToStaticMarkup(React.createElement('div', null,
    React.createElement(GlobalSearch, { onSelect() {} }),
    React.createElement(NotificationsMenu, { onOpenWorkflow() {} }),
  ))
  assert.match(html, /Search saved SkillFair records/)
  assert.match(html, /Notifications/)
})

test('display preferences are browser-local and scoped to the account ID', () => {
  const originalWindow = globalThis.window
  const values = new Map()
  globalThis.window = { localStorage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) } }
  try {
    assert.equal(readUserPreferences('user-a').displayName, '')
    saveDisplayNamePreference('user-a', '  Alex Example  ')
    assert.equal(readUserPreferences('user-a').displayName, 'Alex Example')
    assert.equal(readUserPreferences('user-b').displayName, '')
  } finally { globalThis.window = originalWindow }
})

test('settings and help explain local preferences and product limits', () => {
  const settings = renderToStaticMarkup(React.createElement(SettingsPage, { ownerId: 'user-a', email: 'alex@example.test', onDisplayNameChange() {} }))
  const help = renderToStaticMarkup(React.createElement(HelpCenter))
  assert.match(settings, /Save on this browser/)
  assert.match(settings, /Theme and notification preferences are not currently supported/)
  assert.match(help, /does not make hiring decisions/)
  assert.match(help, /explicitly consented external AI provider/)
  assert.match(help, /do not demonstrate fairness or improved hiring outcomes/)
})
