import assert from 'node:assert/strict'
import test, { after, before, beforeEach } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ configFile: 'vite.config.ts', appType: 'custom', logLevel: 'error', server: { middlewareMode: true } })
const uploads = await vite.ssrLoadModule('/src/resumeUpload.ts')
const expiry = await vite.ssrLoadModule('/src/sessionExpiry.ts')
after(async () => { await vite.close() })

let originalXhr
before(() => { originalXhr = globalThis.XMLHttpRequest })
after(() => { globalThis.XMLHttpRequest = originalXhr })
beforeEach(() => expiry.resetSessionExpiredNotification())

const response = { analysis_id: 'analysis-reference', analysis_mode: 'local', skills: [], extracted_characters: 20, message: 'Complete.' }

class FakeXhr {
  constructor() { this.upload = {}; this.headers = {}; this.status = 0; this.responseText = ''; this.sendCalls = 0; this.abortCalls = 0 }
  open(method, url) { this.method = method; this.url = url }
  setRequestHeader(name, value) { this.headers[name] = value }
  send(body) { this.sendCalls += 1; this.body = body }
  abort() { this.abortCalls += 1; this.onabort?.() }
  complete(status, body) { this.status = status; this.responseText = JSON.stringify(body); this.onload?.() }
}

function start(options = {}) {
  const xhr = new FakeXhr()
  const file = new File(['resume'], 'resume.pdf', { type: 'application/pdf' })
  const progress = []
  const pending = uploads.uploadResume(file, 'local', false, undefined, (value) => progress.push(value), options.signal, {
    xhrFactory: () => xhr,
    csrfToken: () => 'csrf-test-token',
    timeoutMs: options.timeoutMs ?? 90_000,
  })
  return { xhr, file, pending, progress }
}

test('resume upload sends cookies and CSRF, reports progress, and accepts success', async () => {
  const { xhr, pending, progress, file } = start()
  assert.equal(xhr.method, 'POST')
  assert.equal(xhr.url, '/api/skills/discover')
  assert.equal(xhr.withCredentials, true)
  assert.equal(xhr.headers['X-CSRF-Token'], 'csrf-test-token')
  assert.equal(xhr.timeout, 90_000)
  assert.equal(xhr.body.get('file').name, file.name)
  xhr.upload.onprogress({ lengthComputable: true, loaded: 25, total: 100 })
  xhr.upload.onload()
  xhr.complete(200, response)
  assert.equal((await pending).analysis_id, 'analysis-reference')
  assert.deepEqual(progress, [25, 100])
})

test('resume upload timeout is bounded and tells the user to check before retrying', async () => {
  const { xhr, pending } = start({ timeoutMs: 12_000 })
  assert.equal(xhr.timeout, 12_000)
  xhr.ontimeout()
  await assert.rejects(pending, (error) => error instanceof uploads.ResumeUploadTimeoutError && error.message.includes('may have completed'))
  assert.equal(xhr.sendCalls, 1)
})

test('resume upload reports network and server errors without automatic retries', async () => {
  const network = start()
  network.xhr.onerror()
  await assert.rejects(network.pending, /Could not reach the Skills Discovery service/)
  const server = start()
  server.xhr.complete(503, { detail: 'Temporarily unavailable.' })
  await assert.rejects(server.pending, /Temporarily unavailable/)
  assert.equal(network.xhr.sendCalls, 1)
  assert.equal(server.xhr.sendCalls, 1)
})

test('resume upload reports an expired session through the shared expiry handler', async () => {
  let expirations = 0
  const unregister = expiry.registerSessionExpiredHandler(() => { expirations += 1 })
  try {
    const { xhr, pending } = start()
    xhr.complete(401, { detail: 'Authentication required.' })
    await assert.rejects(pending, /sign-in session has expired/)
    assert.equal(expirations, 1)
  } finally { unregister() }
})

test('manual abort cancels the XHR and does not surface a stale success', async () => {
  const controller = new AbortController()
  const { xhr, pending } = start({ signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, (error) => error.name === 'AbortError')
  xhr.complete(200, response)
  assert.equal(xhr.abortCalls, 1)
  assert.equal(xhr.sendCalls, 1)
})

test('account changes abort uploads before a late response can be accepted', async () => {
  const { xhr, pending } = start()
  expiry.resetSessionExpiredNotification()
  await assert.rejects(pending, (error) => error.name === 'ApiSessionChangedError')
  xhr.complete(200, response)
  assert.equal(xhr.abortCalls, 1)
})
