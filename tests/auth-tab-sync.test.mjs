import assert from 'node:assert/strict'
import test from 'node:test'
import { AuthApiError } from '../src/authClient.ts'
import { createSessionRevalidator } from '../src/authSessionRevalidation.ts'

class FakeBroadcastChannel {
  static channels = new Set()
  constructor(name) { this.name = name; this.listeners = new Set(); FakeBroadcastChannel.channels.add(this) }
  addEventListener(type, listener) { if (type === 'message') this.listeners.add(listener) }
  removeEventListener(type, listener) { if (type === 'message') this.listeners.delete(listener) }
  postMessage(data) {
    if (this.failNextPost) { this.failNextPost = false; throw new Error('channel unavailable') }
    for (const target of FakeBroadcastChannel.channels) {
      if (target !== this && target.name === this.name) {
        for (const listener of target.listeners) listener({ data })
      }
    }
  }
  close() { FakeBroadcastChannel.channels.delete(this) }
}

globalThis.BroadcastChannel = FakeBroadcastChannel
globalThis.window = new EventTarget()
const localStorageValues = new Map()
globalThis.window.localStorage = {
  setItem(key, value) {
    localStorageValues.set(key, value)
    const event = new Event('storage')
    Object.defineProperties(event, { key: { value: key }, newValue: { value } })
    globalThis.window.dispatchEvent(event)
  },
}
const { publishAuthTabChange, subscribeToAuthTabChanges } = await import('../src/authTabSync.ts')

test('cross-tab notification carries only a generic session-change marker', () => {
  const received = []
  const stop = subscribeToAuthTabChanges(() => received.push('revalidate'))
  const otherTab = new FakeBroadcastChannel('skillfair-auth-state-v1')
  const messages = []
  otherTab.addEventListener('message', (event) => messages.push(event.data))
  try {
    otherTab.postMessage('session-changed')
    assert.deepEqual(received, ['revalidate'])
    publishAuthTabChange()
    assert.deepEqual(messages, ['session-changed'])
    assert.equal(JSON.stringify(messages).includes('account-'), false)
  } finally {
    stop()
    otherTab.close()
  }
})

test('storage fallback reaches listening tabs when BroadcastChannel publish fails', () => {
  let notifications = 0
  const stop = subscribeToAuthTabChanges(() => { notifications += 1 })
  const activeChannel = [...FakeBroadcastChannel.channels].find((item) => item.name === 'skillfair-auth-state-v1')
  assert.ok(activeChannel)
  activeChannel.failNextPost = true
  try {
    publishAuthTabChange()
    assert.equal(notifications, 1)
  } finally {
    stop()
  }
})

test('cross-tab logout notification triggers /me revalidation before clearing identity', async () => {
  let status = 'authenticated'
  let userId = 'account-a'
  let requestCount = 0
  const revalidator = createSessionRevalidator({
    async loadUser() { requestCount += 1; throw new AuthApiError('expired', 401) },
    onStart() { status = 'loading'; userId = '' },
    onAuthenticated(user) { status = 'authenticated'; userId = user.id },
    onAnonymous() { status = 'anonymous'; userId = '' },
    onError() { status = 'error'; userId = '' },
    isUnauthorized(reason) { return reason instanceof AuthApiError && reason.status === 401 },
  })
  let pending = Promise.resolve()
  const stop = subscribeToAuthTabChanges(() => { pending = revalidator.revalidate() })
  const otherTab = new FakeBroadcastChannel('skillfair-auth-state-v1')
  try {
    otherTab.postMessage('session-changed')
    assert.equal(status, 'loading')
    await pending
    assert.equal(requestCount, 1)
    assert.equal(status, 'anonymous')
    assert.equal(userId, '')
  } finally {
    stop()
    otherTab.close()
  }
})

test('cross-tab account switch notification installs only the /me-confirmed account', async () => {
  let status = 'authenticated'
  let userId = 'account-a'
  let requestCount = 0
  const revalidator = createSessionRevalidator({
    async loadUser() { requestCount += 1; return { id: 'account-b', email: 'b@example.test', created_at: '2026-11-01T00:00:00Z' } },
    onStart() { status = 'loading'; userId = '' },
    onAuthenticated(user) { status = 'authenticated'; userId = user.id },
    onAnonymous() { status = 'anonymous'; userId = '' },
    onError() { status = 'error'; userId = '' },
    isUnauthorized(reason) { return reason instanceof AuthApiError && reason.status === 401 },
  })
  let pending = Promise.resolve()
  const stop = subscribeToAuthTabChanges(() => { pending = revalidator.revalidate() })
  const otherTab = new FakeBroadcastChannel('skillfair-auth-state-v1')
  try {
    otherTab.postMessage('session-changed')
    assert.equal(status, 'loading')
    await pending
    assert.equal(requestCount, 1)
    assert.equal(status, 'authenticated')
    assert.equal(userId, 'account-b')
  } finally {
    stop()
    otherTab.close()
  }
})

