import assert from 'node:assert/strict'
import test, { after, before, beforeEach } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: 'vite.config.ts',
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
})
const api = await vite.ssrLoadModule('/src/apiClient.ts')
const expiry = await vite.ssrLoadModule('/src/sessionExpiry.ts')
const auth = await vite.ssrLoadModule('/src/authClient.ts')
after(async () => { await vite.close() })

let originalFetch
before(() => { originalFetch = globalThis.fetch })
after(() => { globalThis.fetch = originalFetch })
beforeEach(() => {
  expiry.resetSessionExpiredNotification()
  globalThis.fetch = originalFetch
})

test('feature requests include cookies and preserve normal responses', async () => {
  globalThis.fetch = async (_input, init) => {
    assert.equal(init.credentials, 'include')
    assert.ok(init.signal instanceof AbortSignal)
    return new Response('{"ok":true}', { status: 200 })
  }
  const response = await api.skillFairFetch('/api/impact/summary')
  assert.equal(response.status, 200)
})

test('401 from Impact, Skills Discovery, and Career Orchestrator clears authenticated UI state once', async () => {
  let state = { user: { id: 'account-a' }, privatePage: 'evidence' }
  let expirations = 0
  const unregister = expiry.registerSessionExpiredHandler(() => {
    expirations += 1
    state = { user: null, privatePage: null }
  })
  globalThis.fetch = async () => new Response('{"detail":"Authentication required."}', { status: 401 })
  try {
    const responses = await Promise.all([
      api.skillFairFetch('/api/impact/summary'),
      api.skillFairFetch('/api/skills/analyses/latest'),
      api.skillFairFetch('/api/career/orchestrator/runs/550e8400e29b41d4a716446655440000'),
    ])
    assert.deepEqual(responses.map((response) => response.status), [401, 401, 401])
    assert.deepEqual(state, { user: null, privatePage: null })
    assert.equal(expirations, 1)
  } finally {
    unregister()
  }
})

test('login failure does not notify the feature-session expiry handler', async () => {
  let expirations = 0
  const unregister = expiry.registerSessionExpiredHandler(() => { expirations += 1 })
  try {
    await assert.rejects(
      auth.loginAccount('recruiter@example.test', 'wrong-password', async () => new Response('{}', { status: 401 })),
      (error) => error.status === 401,
    )
    assert.equal(expirations, 0)
    globalThis.fetch = async () => new Response('{}', { status: 401 })
    const response = await api.skillFairFetch('/api/auth/login', { method: 'POST', headers: { 'X-CSRF-Token': 'test-token' } })
    assert.equal(response.status, 401)
    assert.equal(expirations, 0)
  } finally {
    unregister()
  }
})

test('a late 401 from an earlier account session is discarded without expiring the new session', async () => {
  let expirations = 0
  const unregister = expiry.registerSessionExpiredHandler(() => { expirations += 1 })
  let respond
  globalThis.fetch = async () => new Promise((resolve) => { respond = resolve })
  try {
    const oldRequest = api.skillFairFetch('/api/skills/analyses/latest')
    expiry.resetSessionExpiredNotification()
    respond(new Response('{}', { status: 401 }))
    await assert.rejects(oldRequest, (error) => error instanceof api.ApiSessionChangedError)
    assert.equal(expirations, 0)
  } finally {
    unregister()
  }
})

test('a late successful response from an earlier account session is rejected', async () => {
  globalThis.fetch = async () => new Response('{"account":"account-a"}', { status: 200 })
  const request = api.skillFairFetch('/api/skills/analyses/latest')
  expiry.resetSessionExpiredNotification()
  await assert.rejects(request, (error) => error instanceof api.ApiSessionChangedError)
})

test('caller cancellation and already-aborted signals reach fetch', async () => {
  globalThis.fetch = async (_input, init) => new Promise((_resolve, reject) => {
    if (init.signal.aborted) reject(init.signal.reason)
    else init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true })
  })
  const controller = new AbortController()
  const pending = api.skillFairFetch('/api/impact/summary', { signal: controller.signal })
  controller.abort(new DOMException('Caller canceled', 'AbortError'))
  await assert.rejects(pending, (error) => error.name === 'AbortError')

  const alreadyAborted = new AbortController()
  alreadyAborted.abort(new DOMException('Already canceled', 'AbortError'))
  await assert.rejects(
    api.skillFairFetch('/api/impact/summary', { signal: alreadyAborted.signal }),
    (error) => error.name === 'AbortError',
  )
})

test('shared timeout still aborts when the caller also supplies a signal', async () => {
  const realSetTimeout = globalThis.setTimeout
  const realClearTimeout = globalThis.clearTimeout
  let timeoutCallback
  globalThis.setTimeout = (callback, delay, ...args) => {
    if (delay === 20_000) { timeoutCallback = callback; return 81 }
    return realSetTimeout(callback, delay, ...args)
  }
  globalThis.clearTimeout = (id) => id === 81 ? undefined : realClearTimeout(id)
  globalThis.fetch = async (_input, init) => new Promise((_resolve, reject) => {
    if (init.signal.aborted) reject(new DOMException('Aborted', 'AbortError'))
    else init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
  })
  try {
    const pending = api.skillFairFetch('/api/impact/summary', { signal: new AbortController().signal })
    timeoutCallback()
    await assert.rejects(pending, (error) => error instanceof api.ApiRequestTimeoutError)
  } finally {
    globalThis.setTimeout = realSetTimeout
    globalThis.clearTimeout = realClearTimeout
  }
})

test('shared timeout remains active until a response body finishes', async () => {
  const realSetTimeout = globalThis.setTimeout
  const realClearTimeout = globalThis.clearTimeout
  let timeoutCallback
  let cleared = 0
  globalThis.setTimeout = (callback, delay, ...args) => {
    if (delay === 20_000) { timeoutCallback = callback; return 82 }
    return realSetTimeout(callback, delay, ...args)
  }
  globalThis.clearTimeout = (id) => {
    if (id === 82) { cleared += 1; return }
    realClearTimeout(id)
  }
  globalThis.fetch = async () => new Response(new ReadableStream({
    pull() { return new Promise(() => undefined) },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  try {
    const response = await api.skillFairFetch('/api/impact/summary', { signal: new AbortController().signal })
    const body = response.json()
    timeoutCallback()
    await assert.rejects(body, (error) => error instanceof api.ApiRequestTimeoutError)
    assert.equal(cleared, 1)
  } finally {
    globalThis.setTimeout = realSetTimeout
    globalThis.clearTimeout = realClearTimeout
  }
})

test('account epoch changes abort a response body that is still being read', async () => {
  globalThis.fetch = async () => new Response(new ReadableStream({
    pull() { return new Promise(() => undefined) },
  }), { status: 200 })
  const response = await api.skillFairFetch('/api/skills/analyses/latest')
  const body = response.text()
  expiry.resetSessionExpiredNotification()
  await assert.rejects(body, (error) => error instanceof api.ApiSessionChangedError)
})
