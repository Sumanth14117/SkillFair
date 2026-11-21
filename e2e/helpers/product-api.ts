import type { BrowserContext, Route } from '@playwright/test'
import { accountA, accountB } from './auth-api'

const NOW = '2026-11-20T10:00:00Z'
const RUN_A = '550e8400e29b41d4a716446655440001'
const ANALYSIS_A = '550e8400e29b41d4a716446655441001'

type AccountId = typeof accountA.id | typeof accountB.id
type StoredWorkflow = {
  run: ReturnType<typeof workflow>
  inputs: { job_title: string; job_description: string; geography: string | null; essential_skills: string[]; preferred_skills: string[] }
  updatedAt: string
}
type StoredAudit = {
  audit_id: string; job_title: string | null; created_at: string; updated_at: string
  criteria_reviewed: number; finding_count: number; reviewed_finding_count: number
  result: ReturnType<typeof auditResult>; review_selections: Array<{ finding_index: number; review_state: string; updated_at: string }>
}
type RequestRecord = { method: string; path: string; userId?: AccountId; status?: number; payload?: unknown }
type Gate = { path: string; userId?: AccountId; start: () => void; started: Promise<void>; release: () => void; wait: Promise<void> }

function accountForCookie(cookie: string | null | undefined): AccountId | undefined {
  if (!cookie) return undefined
  if (cookie.includes(accountA.id)) return accountA.id
  if (cookie.includes(accountB.id)) return accountB.id
  return undefined
}

function cookieValue(cookieHeader: string | undefined, name: string): string | null {
  const prefix = `${name}=`
  const item = cookieHeader?.split(';').map((part) => part.trim()).find((part) => part.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : null
}

async function json(route: Route, status: number, body: unknown): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(body) })
}

function skillEvidence() {
  return [{
    skill_name: 'Python', evidence_type: 'explicit', detection_method: 'local', validation_status: 'evidence_found',
    supporting_excerpt: 'Built a Python data pipeline.', relevance_explanation: 'Python is explicitly mentioned in a project description.',
  }]
}

function analysisFor(userId: AccountId) {
  return {
    analysis_id: userId === accountA.id ? ANALYSIS_A : '550e8400e29b41d4a716446655442002',
    analysis_mode: 'local', created_at: NOW, skills: skillEvidence(),
  }
}

function workflow(runId: string, inputs: StoredWorkflow['inputs'], decision: 'proceed' | 'revise' | 'stop' | null = null) {
  const complete = decision === 'proceed'
  const workflowStatus = complete ? 'complete' : decision === 'revise' ? 'revise_required' : decision === 'stop' ? 'stopped' : 'awaiting_human_review'
  const humanRecorded = decision !== null
  const requirement = inputs.essential_skills[0] ?? inputs.preferred_skills[0] ?? 'Python'
  const stage = (name: string, status: string, summary: string) => ({ stage: name, status, summary })
  return {
    run_id: runId, started_at: NOW, workflow_status: workflowStatus,
    stages: [
      stage('skills_discovery', 'complete', 'Resolved one validated skill record.'),
      stage('market_intelligence', 'unavailable', 'No approved provider data is configured.'),
      stage('job_audit', 'complete', 'Completed advisory criteria review.'),
      stage('inclusive_matching', 'complete', 'Compared role criteria with validated evidence.'),
      stage('human_review', humanRecorded ? 'complete' : 'needs_human_review', 'Recruiter review is required.'),
      stage('learning_pathways', complete ? 'complete' : decision === 'stop' ? 'stopped' : 'blocked_pending_review', complete ? 'Learning activities generated after review.' : 'Held for human review.'),
    ],
    skill_passport: {
      analysis_id: ANALYSIS_A, analysis_mode: 'local', skill_count: 1, explicit_count: 1, inferred_count: 0,
      skills: [{ skill_name: 'Python', evidence_type: 'explicit', validation_status: 'evidence_found', detection_method: 'local' }],
    },
    market_intelligence: { job_title: inputs.job_title, geography: inputs.geography, data_availability: 'unavailable', freshness: 'not_available', sources: [], insights: [], message: 'No verified provider is configured.' },
    job_audit: { job_title: inputs.job_title, criteria_reviewed: 1, finding_count: 0, findings: [], human_review_required: true, disclaimer: 'Advisory review only.' },
    inclusive_matching: {
      job_title: inputs.job_title,
      requirements: [{ skill: requirement, importance: 'essential', status: 'explicitly_evidenced', evidence: [], explanation: 'Synthetic test evidence is shown only to exercise the interface.' }],
      exact_explicit_count: 1, exact_inferred_count: 0, potential_transfer_count: 0, not_evidenced_count: 0,
      bias_audit_findings: [], human_review_prompts: ['Review the supplied evidence.'], human_review_required: true, disclaimer: 'No automated hiring decision is made.',
    },
    human_review: {
      status: humanRecorded ? 'recorded' : 'pending', decision, reviewable_requirement_ids: ['req-001'],
      reviewed_requirement_ids: humanRecorded ? ['req-001'] : [], review_notes: null,
      attested_at: humanRecorded ? NOW : null, reviewer_identity_verified: false,
    },
    learning_pathways: complete ? {
      status: 'complete', message: 'Activities follow a committed Proceed decision.',
      result: {
        job_title: inputs.job_title, analysis_linked: true, requirement_count: 1,
        pathways: [{ required_skill: requirement, evidence_status: 'explicitly_evidenced', evidenced_skill: 'Python', evidence_type: 'explicit', validation_status: 'evidence_found', detection_method: 'local', activities: [{ activity_type: 'practice_exercise', title: 'Build a small project', description: 'Complete a synthetic practice task.', relevance: 'Practice a listed requirement.' }] }],
        disclaimer: 'Synthetic browser-test content; learning activities are advisory.',
      },
    } : { status: decision === 'stop' ? 'stopped' : 'blocked_pending_review', message: decision === 'stop' ? 'The workflow was stopped.' : 'Learning activities are held for human review.', result: null },
    workflow_summary: decision ? `Synthetic workflow decision recorded: ${decision}.` : 'Synthetic workflow awaiting a human review decision.',
    disclaimers: ['Browser-test content is synthetic.', 'Missing evidence does not prove missing ability.'],
  }
}

function auditResult(jobTitle: string | null) {
  const finding = {
    rule_id: 'BA-EXPERIENCE-CONTINUITY-001', category: 'experience_threshold', priority: 'medium',
    triggering_phrase: 'Minimum 8 years of continuous experience', criterion_importance: 'essential',
    explanation: 'Rigid continuity wording may exclude people with career breaks; consider assessing relevant skills and experience.',
    suggested_revision_or_action: 'Evaluate relevant skills and demonstrated experience instead.',
    status: 'potential_concern_for_human_review', human_review_required: true,
  }
  return { job_title: jobTitle, criteria_reviewed: 1, finding_count: 1, findings: [finding], human_review_required: true, disclaimer: 'Potential concern for human review; this does not establish fairness or legal compliance.' }
}

function auditSummary(audit: StoredAudit) {
  return {
    audit_id: audit.audit_id, job_title: audit.job_title, created_at: audit.created_at, updated_at: audit.updated_at,
    criteria_reviewed: audit.criteria_reviewed, finding_count: audit.finding_count,
    reviewed_finding_count: audit.review_selections.filter((item) => item.review_state !== 'needs_review').length,
  }
}

function impactSummary(available: boolean) {
  const metrics = [
    { metric_id: 'review_duration_average', name: 'Average recruiter review time', category: 'operations', value: available ? 312 : null, unit: 'seconds', sample_size: available ? 6 : 0, data_availability: available ? 'measured' : 'awaiting_data' },
    { metric_id: 'recruiter_evidence_usefulness', name: 'Evidence usefulness', category: 'recruiter_feedback', value: available ? 4 : null, unit: 'rating_1_to_5', sample_size: available ? 5 : 0, data_availability: available ? 'measured' : 'awaiting_data' },
    { metric_id: 'candidate_application_experience', name: 'Candidate application experience', category: 'candidate_feedback', value: null, unit: 'rating_1_to_5', sample_size: 0, data_availability: 'awaiting_data' },
    { metric_id: 'completed_reviews', name: 'Completed reviews', category: 'operations', value: available ? 3 : 0, unit: 'count', sample_size: available ? 3 : 0, data_availability: available ? 'measured' : 'awaiting_data' },
  ].map((metric) => ({ period_start: null, period_end: null, methodology: 'Synthetic response used only in browser tests.', limitations: ['This response is not a hiring outcome or fairness measure.'], ...metric }))
  const noDistribution = ['clarity', 'accessibility', 'communication', 'other'].map((category) => ({ label: category, value: null, sample_size: 0, data_availability: 'awaiting_data' }))
  const candidateFeedbackDistributions = [
    ['candidate_application_experience', 'Application experience'], ['candidate_process_accessibility', 'Process accessibility'],
    ['candidate_job_requirements_clarity', 'Job requirements clarity'], ['candidate_perceived_fairness', 'Perceived fairness'],
  ].map(([metric_id, name]) => ({ metric_id, name, sample_size: 0, data_availability: 'awaiting_data', period_start: null, period_end: null, distribution: [1, 2, 3, 4, 5].map((rating) => ({ label: String(rating), value: null, sample_size: 0, data_availability: 'awaiting_data' })) }))
  return {
    generated_at: NOW, requested_start_date: null, requested_end_date: null, category: 'all', pilot_period_label: 'Synthetic test period',
    retention_seconds: 7_776_000, dropped_event_count: 0, minimum_feedback_sample: 5, metrics,
    decision_distribution: ['proceed', 'revise', 'stop'].map((label) => ({ label, value: available ? 1 : null, sample_size: available ? 3 : 0, data_availability: available ? 'measured' : 'awaiting_data' })),
    review_duration_trend: [], recruiter_rating_distribution: [], candidate_rating_distribution: [],
    candidate_feedback_distributions: candidateFeedbackDistributions,
    candidate_suggestion_distribution: noDistribution,
    baseline_comparison: { data_availability: 'awaiting_data', baseline_average_seconds: null, pilot_average_seconds: null, absolute_difference_seconds: null, percentage_difference: null, baseline_sample_size: 0, pilot_sample_size: 0, baseline_period_start: null, baseline_period_end: null, pilot_period_start: null, pilot_period_end: null, limitations: ['Synthetic browser-test response.'] },
    disclaimer: 'Synthetic browser-test aggregate only. This does not demonstrate fairness or improved hiring outcomes.',
  }
}

export function buildEmptyImpactSummary() {
  return impactSummary(false)
}

function notification(id: string, readAt: string | null = null) {
  return { notification_id: id, notification_type: 'workflow_decision', title: 'Review decision recorded', message: 'A synthetic test workflow decision was recorded.', created_at: NOW, read_at: readAt, workflow_id: RUN_A }
}

export type ProductApiMock = {
  requests: RequestRecord[]
  pauseNext: (path: string, userId?: AccountId) => { started: Promise<void>; release: () => void }
  dispose: () => Promise<void>
}

/** Stateful synthetic product APIs. Unhandled requests fall through to the auth mock's local 404, never to a backend. */
export async function installProductApiMock(context: BrowserContext): Promise<ProductApiMock> {
  const requests: RequestRecord[] = []
  const analyses = new Map<AccountId, ReturnType<typeof analysisFor>>()
  const workflows = new Map<AccountId, Map<string, StoredWorkflow>>()
  const audits = new Map<AccountId, Map<string, StoredAudit>>()
  const notifications = new Map<AccountId, Array<ReturnType<typeof notification>>>()
  const pauses: Gate[] = []
  let runNumber = 0
  let auditNumber = 0

  const pauseNext: ProductApiMock['pauseNext'] = (path, userId) => {
    let start!: () => void
    let release!: () => void
    const started = new Promise<void>((resolve) => { start = resolve })
    const wait = new Promise<void>((resolve) => { release = resolve })
    pauses.push({ path, userId, start, started, release, wait })
    return { started, release }
  }

  const handler = async (route: Route): Promise<void> => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const userId = accountForCookie(cookieValue(request.headers().cookie, 'skillfair_session'))
    const record: RequestRecord = { method, path, userId }
    requests.push(record)

    const pauseIndex = pauses.findIndex((gate) => gate.path === path && (!gate.userId || gate.userId === userId))
    if (pauseIndex >= 0) {
      const [gate] = pauses.splice(pauseIndex, 1)
      gate.start()
      await gate.wait
    }

    const protectedRoute = async () => {
      if (!userId) { record.status = 401; await json(route, 401, { detail: 'Authentication required.' }); return false }
      return true
    }

    if (path === '/api/auth/me' || path.startsWith('/api/auth/')) { await route.fallback(); return }
    if (path === '/api/skills/provider' && method === 'GET') { record.status = 200; await json(route, 200, { provider: 'none', configured: false, message: 'No AI provider is configured.' }); return }
    if (path === '/api/skills/discover' && method === 'POST') {
      if (!await protectedRoute()) return
      const analysis = analysisFor(userId!)
      analyses.set(userId!, analysis)
      record.status = 200; record.payload = { analysis_mode: 'local' }
      await json(route, 200, { ...analysis, extracted_characters: 31, message: 'Synthetic local analysis completed.' }); return
    }
    if (path === '/api/skills/analyses/latest' && method === 'GET') {
      if (!await protectedRoute()) return
      const analysis = analyses.get(userId!)
      record.status = 200; await json(route, 200, analysis ?? null); return
    }

    if (path === '/api/career/orchestrator/runs' && method === 'GET') {
      if (!await protectedRoute()) return
      const items = [...(workflows.get(userId!)?.entries() ?? [])].map(([runId, stored]) => ({
        run_id: runId, job_title: stored.inputs.job_title, workflow_status: stored.run.workflow_status,
        review_decision: stored.run.human_review.decision, created_at: stored.run.started_at, updated_at: stored.updatedAt,
      })).sort((a, b) => b.created_at.localeCompare(a.created_at))
      record.status = 200; await json(route, 200, items); return
    }
    if (path === '/api/career/orchestrator/runs' && method === 'POST') {
      if (!await protectedRoute()) return
      const payload = request.postDataJSON() as StoredWorkflow['inputs'] & { analysis_id?: string }
      runNumber += 1
      const runId = runNumber === 1 ? RUN_A : `550e8400e29b41d4a71644665544${String(runNumber).padStart(4, '0')}`
      const inputs = { job_title: payload.job_title, job_description: payload.job_description, geography: payload.geography ?? null, essential_skills: payload.essential_skills ?? [], preferred_skills: payload.preferred_skills ?? [] }
      const stored = { run: workflow(runId, inputs), inputs, updatedAt: NOW }
      const userRuns = workflows.get(userId!) ?? new Map<string, StoredWorkflow>()
      userRuns.set(runId, stored); workflows.set(userId!, userRuns)
      record.status = 200; record.payload = payload; await json(route, 200, stored.run); return
    }
    const runMatch = path.match(/^\/api\/career\/orchestrator\/runs\/([a-f0-9]{32})(?:\/(review))?$/)
    if (runMatch && method === 'GET') {
      if (!await protectedRoute()) return
      const stored = workflows.get(userId!)?.get(runMatch[1])
      if (!stored) { record.status = 404; await json(route, 404, { detail: 'Career workflow was not found.' }); return }
      record.status = 200; await json(route, 200, { ...stored.run, workflow_inputs: stored.inputs }); return
    }
    if (runMatch && method === 'POST' && runMatch[2] === 'review') {
      if (!await protectedRoute()) return
      const stored = workflows.get(userId!)?.get(runMatch[1])
      if (!stored) { record.status = 404; await json(route, 404, { detail: 'Career workflow was not found.' }); return }
      const payload = request.postDataJSON() as { decision: 'proceed' | 'revise' | 'stop' }
      stored.run = workflow(runMatch[1], stored.inputs, payload.decision)
      stored.updatedAt = NOW
      record.status = 200; record.payload = payload
      const notice = notification(`notification-${userId}-${runMatch[1]}`)
      const accountNotifications = notifications.get(userId!) ?? []
      if (!accountNotifications.some((item) => item.notification_id === notice.notification_id)) accountNotifications.unshift(notice)
      notifications.set(userId!, accountNotifications)
      await json(route, 200, stored.run); return
    }

    if (path === '/api/jobs/audit' && method === 'POST') {
      if (!await protectedRoute()) return
      const payload = request.postDataJSON() as { job_title?: string }
      record.status = 200; record.payload = payload; await json(route, 200, auditResult(payload.job_title ?? null)); return
    }
    if (path === '/api/jobs/audits' && method === 'GET') {
      if (!await protectedRoute()) return
      const saved = [...(audits.get(userId!)?.values() ?? [])].map(auditSummary)
      record.status = 200; await json(route, 200, { audits: saved }); return
    }
    if (path === '/api/jobs/audits' && method === 'POST') {
      if (!await protectedRoute()) return
      const payload = request.postDataJSON() as { job_title?: string }
      auditNumber += 1
      const result = auditResult(payload.job_title ?? null)
      const audit: StoredAudit = {
        audit_id: `a${String(auditNumber).padStart(31, '0')}`, job_title: payload.job_title ?? null, created_at: NOW, updated_at: NOW,
        criteria_reviewed: result.criteria_reviewed, finding_count: result.finding_count, reviewed_finding_count: 0,
        result, review_selections: [{ finding_index: 0, review_state: 'needs_review', updated_at: NOW }],
      }
      const userAudits = audits.get(userId!) ?? new Map<string, StoredAudit>()
      userAudits.set(audit.audit_id, audit); audits.set(userId!, userAudits)
      record.status = 200; record.payload = payload; await json(route, 200, { ...audit, ...auditSummary(audit) }); return
    }
    const auditMatch = path.match(/^\/api\/jobs\/audits\/([a-f0-9]{32})(?:\/findings\/(\d+))?$/)
    if (auditMatch && method === 'GET') {
      if (!await protectedRoute()) return
      const audit = audits.get(userId!)?.get(auditMatch[1])
      if (!audit) { record.status = 404; await json(route, 404, { detail: 'Saved audit was not found.' }); return }
      record.status = 200; await json(route, 200, { ...audit, ...auditSummary(audit) }); return
    }
    if (auditMatch && method === 'PATCH' && auditMatch[2]) {
      if (!await protectedRoute()) return
      const audit = audits.get(userId!)?.get(auditMatch[1])
      if (!audit) { record.status = 404; await json(route, 404, { detail: 'Saved audit was not found.' }); return }
      const payload = request.postDataJSON() as { review_state: string }
      audit.review_selections = [{ finding_index: Number(auditMatch[2]), review_state: payload.review_state, updated_at: NOW }]
      audit.updated_at = NOW
      record.status = 200; record.payload = payload; await json(route, 200, { ...audit, ...auditSummary(audit) }); return
    }

    if (path === '/api/impact/summary' && method === 'GET') {
      if (!await protectedRoute()) return
      record.status = 200; await json(route, 200, impactSummary(url.searchParams.get('category') !== 'candidate_feedback')); return
    }
    if (['/api/impact/feedback/recruiter', '/api/impact/feedback/candidate'].includes(path) && method === 'POST') {
      if (!await protectedRoute()) return
      record.status = 200; record.payload = request.postDataJSON()
      await json(route, 200, { accepted: true, duplicate: false, message: 'Synthetic feedback was accepted for this browser test.' }); return
    }
    if (path === '/api/impact/baseline-observations' && method === 'POST') {
      if (!await protectedRoute()) return
      record.status = 200; record.payload = request.postDataJSON(); await json(route, 200, { accepted: true, duplicate: false, message: 'Synthetic baseline was accepted.' }); return
    }

    if (path === '/api/market/provider-status' && method === 'GET') {
      record.status = 200; await json(route, 200, { provider: null, availability: 'not_configured', checked_at: null, message: 'No approved market-data provider is configured.' }); return
    }
    if (path === '/api/market/intelligence' && method === 'POST') {
      const payload = request.postDataJSON() as { job_title: string; geography?: string }
      record.status = 200; record.payload = payload
      await json(route, 200, { job_title: payload.job_title, geography: payload.geography ?? null, data_availability: 'unavailable', freshness: 'not_available', sources: [], insights: [], message: 'No approved provider is configured. No market statistics are available.' }); return
    }

    if (path === '/api/notifications' && method === 'GET') {
      if (!await protectedRoute()) return
      const items = notifications.get(userId!) ?? []
      record.status = 200; await json(route, 200, { notifications: items, unread_count: items.filter((item) => !item.read_at).length }); return
    }
    const notificationMatch = path.match(/^\/api\/notifications\/([^/]+)\/read$/)
    if (notificationMatch && method === 'POST') {
      if (!await protectedRoute()) return
      const items = notifications.get(userId!) ?? []
      const item = items.find((entry) => entry.notification_id === notificationMatch[1])
      if (!item) { record.status = 404; await json(route, 404, { detail: 'Notification not found.' }); return }
      item.read_at = NOW; record.status = 200; await json(route, 200, item); return
    }

    await route.fallback()
  }

  await context.route('**/api/**', handler)
  return {
    requests,
    pauseNext,
    dispose: async () => {
      try {
        await context.unroute('**/api/**', handler)
      } catch {
        // Playwright can close a timed-out test context before its finally block.
      }
    },
  }
}

export { RUN_A }
