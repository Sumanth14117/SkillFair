import assert from 'node:assert/strict'
import { test } from 'node:test'

const values = new Map()
globalThis.window = {
  sessionStorage: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  },
}

const { clearSkillFairSession, emptySkillFairSession, readSkillFairSession, writeSkillFairSession } = await import('../src/sessionState.ts')
const ownerA = 'account-a'
const ownerB = 'account-b'

test('persists and restores the backend workflow run ID and recruiter draft', () => {
  const runId = 'generated_run_id-0123456789ABCDEFGHIJKLM'
  const session = emptySkillFairSession(ownerA)
  session.runId = runId
  session.page = 'Career Orchestrator'
  session.careerDraft = {
    jobTitle: 'Data Analyst',
    jobDescription: 'Prepare and explain reports.',
    geography: 'Toronto',
    essentialSkills: 'SQL',
    preferredSkills: 'Python',
  }

  writeSkillFairSession(session)

  const stored = JSON.parse(values.get('skillfair.session.v2'))
  assert.equal(stored.version, 2)
  assert.equal(stored.ownerId, ownerA)
  assert.equal(stored.runId, runId)
  assert.deepEqual(readSkillFairSession(ownerA), session)
})

test('does not persist an empty or malformed run ID as a valid reference', () => {
  const session = emptySkillFairSession(ownerA)
  session.runId = ''
  writeSkillFairSession(session)
  assert.equal(JSON.parse(values.get('skillfair.session.v2')).runId, null)
})

test('restores the Recruiter Review navigation page and workflow locator after refresh', () => {
  const session = emptySkillFairSession(ownerA)
  session.page = 'Recruiter Review'
  session.runId = 'recruiter_review_run_0123456789'

  writeSkillFairSession(session)

  const restored = readSkillFairSession(ownerA)
  assert.equal(restored.page, 'Recruiter Review')
  assert.equal(restored.runId, session.runId)
  assert.equal(restored.analysisId, null)
})

test('account A state is restored only for account A and discarded for account B', () => {
  clearSkillFairSession()
  const session = emptySkillFairSession(ownerA)
  session.page = 'Career Orchestrator'
  session.runId = 'account_a_run_id-0123456789'
  session.careerDraft.jobDescription = 'Account A private role description'
  writeSkillFairSession(session)

  assert.equal(readSkillFairSession(ownerA).careerDraft.jobDescription, 'Account A private role description')
  const accountB = readSkillFairSession(ownerB)
  assert.equal(accountB.ownerId, ownerB)
  assert.equal(accountB.runId, null)
  assert.equal(accountB.careerDraft.jobDescription, '')
  assert.equal(values.has('skillfair.session.v2'), false)
})

test('anonymous transition can clear the account-bound session draft and locators', () => {
  const session = emptySkillFairSession(ownerA)
  session.runId = 'account_a_run_id-0123456789'
  session.careerDraft.jobTitle = 'Account A role'
  writeSkillFairSession(session)

  clearSkillFairSession()

  assert.equal(readSkillFairSession(ownerA).careerDraft.jobTitle, '')
  assert.equal(values.has('skillfair.session.v2'), false)
})

test('unowned legacy session records are removed instead of restored', () => {
  values.set('skillfair.session.v1', JSON.stringify({ version: 1, careerDraft: { jobDescription: 'legacy private draft' } }))
  assert.equal(readSkillFairSession(ownerA).careerDraft.jobDescription, '')
  assert.equal(values.has('skillfair.session.v1'), false)
})
