import assert from 'node:assert/strict'
import test from 'node:test'
import { AuthApiError } from '../src/authClient.ts'
import { createSessionRevalidator } from '../src/authSessionRevalidation.ts'

const accountA = { id: 'account-a', email: 'a@example.test', created_at: '2026-11-01T00:00:00Z' }
const accountB = { id: 'account-b', email: 'b@example.test', created_at: '2026-11-01T00:00:00Z' }

function createStateHarness(loadUser) {
  const state = { status: 'authenticated', user: accountA, privateData: 'account A data' }
  const revalidator = createSessionRevalidator({
    loadUser,
    onStart() { state.status = 'loading'; state.user = null; state.privateData = null },
    onAuthenticated(user) { state.status = 'authenticated'; state.user = user; state.privateData = `${user.id} data` },
    onAnonymous() { state.status = 'anonymous'; state.user = null; state.privateData = null },
    onError() { state.status = 'error'; state.user = null; state.privateData = null },
    isUnauthorized(reason) { return reason instanceof AuthApiError && reason.status === 401 },
  })
  return { state, revalidator }
}

test('Account A login state can restore its own identity-bound draft', async () => {
  const values = new Map()
  globalThis.window = { sessionStorage: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  } }
  const { emptySkillFairSession, readSkillFairSession, writeSkillFairSession } = await import('../src/sessionState.ts')
  const session = emptySkillFairSession(accountA.id)
  session.careerDraft.jobDescription = 'Account A role'
  writeSkillFairSession(session)
  assert.equal(readSkillFairSession(accountA.id).careerDraft.jobDescription, 'Account A role')
})

test('Account A to anonymous transition clears all account state after server revalidation', async () => {
  const { state, revalidator } = createStateHarness(async () => { throw new AuthApiError('expired', 401) })
  await revalidator.revalidate()
  assert.deepEqual(state, { status: 'anonymous', user: null, privateData: null })
})

test('Account A to Account B transition clears A state before accepting B identity', async () => {
  let resolveUser
  const { state, revalidator } = createStateHarness(() => new Promise((resolve) => { resolveUser = resolve }))
  const pending = revalidator.revalidate()
  assert.deepEqual(state, { status: 'loading', user: null, privateData: null })
  resolveUser(accountB)
  await pending
  assert.deepEqual(state, { status: 'authenticated', user: accountB, privateData: 'account-b data' })
})

test('cross-tab logout is detected only after /me confirms anonymous state', async () => {
  let requests = 0
  const { state, revalidator } = createStateHarness(async () => {
    requests += 1
    throw new AuthApiError('expired', 401)
  })
  await revalidator.revalidate()
  assert.equal(requests, 1)
  assert.equal(state.status, 'anonymous')
  assert.equal(state.privateData, null)
})

test('cross-tab account switch revalidates with the server and installs only Account B state', async () => {
  let requests = 0
  const { state, revalidator } = createStateHarness(async () => {
    requests += 1
    return accountB
  })
  await revalidator.revalidate()
  assert.equal(requests, 1)
  assert.equal(state.user.id, accountB.id)
  assert.equal(state.privateData, 'account-b data')
})

test('concurrent cross-tab notifications serialize and coalesce a follow-up validation', async () => {
  let calls = 0
  let active = 0
  let maximumActive = 0
  const resolvers = []
  const { state, revalidator } = createStateHarness(() => {
    calls += 1
    active += 1
    maximumActive = Math.max(maximumActive, active)
    return new Promise((resolve) => resolvers.push((user) => { active -= 1; resolve(user) }))
  })
  const first = revalidator.revalidate()
  const concurrentA = revalidator.revalidate()
  const concurrentB = revalidator.revalidate()
  assert.equal(first, concurrentA)
  assert.equal(first, concurrentB)
  assert.equal(calls, 1)
  resolvers.shift()(accountB)
  await Promise.resolve()
  await Promise.resolve()
  assert.equal(calls, 2)
  resolvers.shift()(accountB)
  await first
  assert.equal(maximumActive, 1)
  assert.equal(state.user.id, accountB.id)
})

test('an older Account A validation cannot overwrite a newer Account B validation', async () => {
  const resolvers = []
  const { state, revalidator } = createStateHarness(() => new Promise((resolve) => { resolvers.push(resolve) }))
  const old = revalidator.revalidate()
  revalidator.invalidate()
  const fresh = revalidator.revalidate()
  resolvers[0](accountA)
  await Promise.resolve()
  resolvers[1](accountB)
  await Promise.all([old, fresh])
  assert.equal(state.user.id, accountB.id)
})

test('same-account refresh restores the same user and does not clear their saved draft', async () => {
  const { state, revalidator } = createStateHarness(async () => accountA)
  await revalidator.revalidate()
  assert.equal(state.user.id, accountA.id)
  assert.equal(state.privateData, 'account-a data')
})
