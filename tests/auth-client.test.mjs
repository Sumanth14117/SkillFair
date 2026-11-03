import test from 'node:test'
import assert from 'node:assert/strict'
import {
  AuthApiError,
  csrfTokenFromCookie,
  getCurrentUser,
  loginAccount,
  logoutAccount,
  registerAccount,
} from '../src/authClient.ts'

const user = { id: 'user-id', email: 'recruiter@example.test', created_at: '2026-10-31T00:00:00Z' }
const jsonResponse = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } })

test('current-user request sends cookies and returns only the account profile', async () => {
  let request
  const result = await getCurrentUser(async (url, options) => {
    request = { url, options }
    return jsonResponse(user)
  })
  assert.equal(request.url, '/api/auth/me')
  assert.equal(request.options.credentials, 'include')
  assert.equal(request.options.method, 'GET')
  assert.deepEqual(result, user)
})

test('concurrent session restoration calls share one request', async () => {
  let calls = 0
  let release
  const fetcher = async () => {
    calls += 1
    await new Promise((resolve) => { release = resolve })
    return jsonResponse(user)
  }
  const first = getCurrentUser(fetcher)
  const second = getCurrentUser(fetcher)
  assert.equal(first, second)
  assert.equal(calls, 1)
  release()
  assert.deepEqual(await Promise.all([first, second]), [user, user])
})

test('forced cross-tab revalidation supersedes a pending current-user request safely', async () => {
  const pending = []
  const fetcher = () => new Promise((resolve) => pending.push(resolve))
  const oldRequest = getCurrentUser(fetcher)
  const freshRequest = getCurrentUser(fetcher, true)
  assert.notEqual(oldRequest, freshRequest)
  assert.equal(pending.length, 2)

  pending[0](jsonResponse({ ...user, id: 'account-a' }))
  assert.equal((await oldRequest).id, 'account-a')
  assert.equal(getCurrentUser(fetcher), freshRequest)

  pending[1](jsonResponse({ ...user, id: 'account-b' }))
  assert.equal((await freshRequest).id, 'account-b')
})

test('unauthenticated session restoration returns a controlled 401 error', async () => {
  await assert.rejects(
    getCurrentUser(async () => jsonResponse({ detail: 'Authentication required.' }, 401)),
    (error) => error instanceof AuthApiError && error.status === 401 && error.message === 'Authentication required.',
  )
})

test('aborted authentication requests receive a safe timeout message', async () => {
  const abortError = new Error('request canceled')
  abortError.name = 'AbortError'
  await assert.rejects(
    getCurrentUser(async () => { throw abortError }),
    (error) => error instanceof AuthApiError && error.status === 0 && error.message === 'The authentication request timed out. Please try again.',
  )
})

test('registration posts credentials without storing or returning session tokens', async () => {
  let request
  const result = await registerAccount('recruiter@example.test', 'sample-safe-password', async (url, options) => {
    request = { url, options }
    return jsonResponse({ message: 'Registration request processed. If the address already has an account, sign in. No verification email is sent.' }, 202)
  })
  assert.equal(request.url, '/api/auth/register')
  assert.equal(request.options.credentials, 'include')
  assert.equal(request.options.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(request.options.body), { email: 'recruiter@example.test', password: 'sample-safe-password' })
  assert.equal(result, undefined)
})

test('login sends credentials with cookie support', async () => {
  let request
  await loginAccount('recruiter@example.test', 'sample-safe-password', async (url, options) => {
    request = { url, options }
    return jsonResponse(user)
  })
  assert.equal(request.url, '/api/auth/login')
  assert.equal(request.options.credentials, 'include')
  assert.equal(request.options.method, 'POST')
})

test('logout reads only the CSRF cookie and sends its header with cookies', async () => {
  assert.equal(csrfTokenFromCookie('other=value; skillfair_csrf=csrf%2Btoken'), 'csrf+token')
  let request
  await logoutAccount(async (url, options) => {
    request = { url, options }
    return new Response(null, { status: 204 })
  }, 'skillfair_csrf=csrf-token')
  assert.equal(request.url, '/api/auth/logout')
  assert.equal(request.options.credentials, 'include')
  assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-token')
  assert.equal(request.options.headers.Cookie, undefined)
})

test('missing CSRF cookie blocks logout before a request is sent', async () => {
  let called = false
  await assert.rejects(
    logoutAccount(async () => { called = true; return new Response(null, { status: 204 }) }, 'skillfair_session=not-readable'),
    (error) => error instanceof AuthApiError && error.status === 403,
  )
  assert.equal(called, false)
})

test('unrecognized server error text is not reflected into authentication UI errors', async () => {
  await assert.rejects(
    loginAccount('recruiter@example.test', 'password', async () => jsonResponse({ detail: 'internal secret or stack details' }, 500)),
    (error) => error instanceof AuthApiError && error.message === 'Authentication is temporarily unavailable. Please try again.',
  )
})
