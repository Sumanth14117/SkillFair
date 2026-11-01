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

const { emptySkillFairSession, readSkillFairSession, writeSkillFairSession } = await import('../src/sessionState.ts')

test('persists and restores the backend workflow run ID and recruiter draft', () => {
  const runId = 'generated_run_id-0123456789ABCDEFGHIJKLM'
  const session = emptySkillFairSession()
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

  const stored = JSON.parse(values.get('skillfair.session.v1'))
  assert.equal(stored.version, 1)
  assert.equal(stored.runId, runId)
  assert.deepEqual(readSkillFairSession(), session)
})

test('does not persist an empty or malformed run ID as a valid reference', () => {
  const session = emptySkillFairSession()
  session.runId = ''
  writeSkillFairSession(session)
  assert.equal(JSON.parse(values.get('skillfair.session.v1')).runId, null)
})

test('restores the Recruiter Review navigation page and workflow locator after refresh', () => {
  const session = emptySkillFairSession()
  session.page = 'Recruiter Review'
  session.runId = 'recruiter_review_run_0123456789'

  writeSkillFairSession(session)

  const restored = readSkillFairSession()
  assert.equal(restored.page, 'Recruiter Review')
  assert.equal(restored.runId, session.runId)
  assert.equal(restored.analysisId, null)
})
