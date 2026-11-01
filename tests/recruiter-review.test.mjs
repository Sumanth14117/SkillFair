import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: 'vite.config.ts',
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
})
const { default: RecruiterReview } = await vite.ssrLoadModule('/src/components/RecruiterReview.tsx')
after(async () => { await vite.close() })

function workflow(decision = null) {
  const complete = decision === 'proceed'
  const reviewed = decision !== null
  return {
    run_id: 'recruiter_review_run_0123456789',
    started_at: '2026-10-30T10:00:00Z',
    workflow_status: complete ? 'complete' : decision === 'revise' ? 'revise_required' : decision === 'stop' ? 'stopped' : 'awaiting_human_review',
    stages: [
      { stage: 'skills_discovery', status: 'complete', summary: 'Resolved one validated skill record.' },
      { stage: 'market_intelligence', status: 'unavailable', summary: 'No verified provider is connected.' },
      { stage: 'job_audit', status: 'complete', summary: 'Completed advisory criteria review.' },
      { stage: 'inclusive_matching', status: 'complete', summary: 'Reviewed role requirements against evidence.' },
      { stage: 'human_review', status: reviewed ? 'complete' : 'needs_human_review', summary: 'Recruiter review required.' },
      { stage: 'learning_pathways', status: complete ? 'complete' : decision === 'stop' ? 'stopped' : 'blocked_pending_review', summary: complete ? 'Learning activities generated after review.' : 'Held for human review.' },
    ],
    skill_passport: {
      analysis_id: 'recruiter_analysis_id_0123456789', analysis_mode: 'local', skill_count: 1,
      explicit_count: 1, inferred_count: 0,
      skills: [{ skill_name: 'SQL', evidence_type: 'explicit', validation_status: 'evidence_found', detection_method: 'local' }],
    },
    market_intelligence: { job_title: 'Data Analyst', geography: null, data_availability: 'unavailable', sources: [], insights: [], message: 'No verified market-data provider is connected.' },
    job_audit: { job_title: 'Data Analyst', criteria_reviewed: 1, finding_count: 0, findings: [], human_review_required: true, disclaimer: 'Advisory only.' },
    inclusive_matching: {
      job_title: 'Data Analyst', requirements: [{ skill: 'SQL', importance: 'essential', status: 'explicitly_evidenced', evidence: [], explanation: 'Exact requirement name match.' }],
      exact_explicit_count: 1, exact_inferred_count: 0, potential_transfer_count: 0, not_evidenced_count: 0,
      bias_audit_findings: [], human_review_prompts: ['Review the requirement evidence.'], human_review_required: true, disclaimer: 'No automatic decisions are made.',
    },
    human_review: {
      status: reviewed ? 'recorded' : 'pending', decision, reviewable_requirement_ids: ['req-001'],
      reviewed_requirement_ids: reviewed ? ['req-001'] : [], review_notes: null,
      attested_at: reviewed ? '2026-10-30T10:05:00Z' : null, reviewer_identity_verified: false,
    },
    learning_pathways: complete ? {
      status: 'complete', message: 'Generated after Proceed.', result: {
        job_title: 'Data Analyst', analysis_linked: true, requirement_count: 1,
        pathways: [{ required_skill: 'SQL', evidence_status: 'explicitly_evidenced', evidenced_skill: 'SQL', evidence_type: 'explicit', validation_status: 'evidence_found', detection_method: 'local', activities: [{ activity_type: 'practice_exercise', title: 'SQL query exercise', description: 'Practice query writing.', relevance: 'Addresses SQL.' }] }],
        disclaimer: 'Advisory learning activities only.',
      },
    } : { status: decision === 'stop' ? 'stopped' : 'blocked_pending_review', message: 'Learning activities are held for review.', result: null },
    workflow_summary: complete ? 'Review recorded; learning activities followed Proceed.' : 'This workflow is awaiting recruiter review.',
    disclaimers: ['Missing evidence does not prove missing ability.', 'No ranking or automated hiring decision is performed.'],
  }
}

function renderReview({ run = null, runId = null } = {}) {
  return renderToStaticMarkup(createElement(RecruiterReview, {
    analysisId: 'recruiter_analysis_id_0123456789',
    run,
    runId,
    onRunChange() {},
    onRunExpired() {},
    onAnalysisExpired() {},
    draft: { jobTitle: '', jobDescription: '', geography: '', essentialSkills: '', preferredSkills: '' },
    onDraftChange() {},
    onSkillsDiscovery() {},
    onOpenOrchestrator() {},
  }))
}

test('Explore workspace links to the functional review workspace', () => {
  const html = renderReview()
  assert.match(html, /href="#recruiter-review-workspace"/)
  assert.match(html, />Explore workspace/)
  assert.match(html, /id="recruiter-review-workspace"/)
})

test('empty review state offers Skills Discovery and Career Orchestrator without sample candidates', () => {
  const html = renderReview()
  assert.match(html, /No workflow is ready for review/)
  assert.match(html, /Go to Skills Discovery/)
  assert.match(html, /Open Career Orchestrator/)
  assert.doesNotMatch(html, /Jordan Lee|Samira Khan|Marcus Reed/)
})

test('saved run ID renders an authoritative restoration/loading state after refresh', () => {
  const html = renderReview({ runId: 'recruiter_review_run_0123456789' })
  assert.match(html, /Restoring workflow/)
  assert.match(html, /Fetching the authoritative workflow state from the backend/)
  assert.doesNotMatch(html, /SQL query exercise/)
})

test('pending workflow exposes guarded human review controls and no learning activities', () => {
  const pending = workflow()
  const html = renderReview({ run: pending })
  assert.match(html, /Review each requirement and mark its identifier/)
  assert.match(html, /Proceed to Learning Pathways/)
  assert.match(html, />\s*Revise</)
  assert.match(html, />\s*Stop</)
  assert.match(html, /blocked pending review/i)
  assert.doesNotMatch(html, /SQL query exercise/)
})

test('completed Proceed workflow shows recorded review and backend-confirmed Learning Pathways', () => {
  const completed = workflow('proceed')
  const html = renderReview({ run: completed })
  assert.match(html, /Decision recorded: <b>proceed<\/b>/)
  assert.match(html, /SQL query exercise/)
  assert.match(html, /Activities are advisory, not qualifications/)
  assert.doesNotMatch(html, /Proceed to Learning Pathways/)
})
