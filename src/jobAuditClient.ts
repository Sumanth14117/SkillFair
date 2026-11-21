import { skillFairFetch } from './apiClient'

export type Importance = 'essential' | 'preferred' | 'unspecified'
export type AuditRequest = {
  job_title?: string
  job_description: string
  requirements: { text: string; importance: Importance }[]
}
export type Finding = {
  rule_id: string
  category: 'credential_requirement' | 'potentially_exclusionary_wording' | 'vague_requirement' | 'experience_threshold'
  priority: 'low' | 'medium' | 'high'
  triggering_phrase: string
  criterion_importance: Importance
  explanation: string
  suggested_revision_or_action: string
  status: 'potential_concern_for_human_review'
  human_review_required: true
}
export type AuditResult = {
  job_title: string | null
  criteria_reviewed: number
  finding_count: number
  findings: Finding[]
  human_review_required: true
  disclaimer: string
}
export type ReviewState = 'needs_review' | 'accepted' | 'edited' | 'dismissed'
export type SavedAuditReview = { finding_index: number; review_state: ReviewState; updated_at: string }
export type SavedAuditSummary = { audit_id: string; job_title: string | null; created_at: string; updated_at: string; criteria_reviewed: number; finding_count: number; reviewed_finding_count: number }
export type SavedAudit = SavedAuditSummary & { result: AuditResult; review_selections: SavedAuditReview[] }
export type SavedAuditList = { audits: SavedAuditSummary[] }

type AuditFetcher = typeof skillFairFetch

export async function submitAudit(payload: AuditRequest, fetcher: AuditFetcher = skillFairFetch, signal?: AbortSignal): Promise<AuditResult> {
  let response: Response
  try {
    response = await fetcher('/api/jobs/audit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    })
  } catch (error) {
    if (error instanceof Error && (error.name === 'AbortError' || error.name === 'ApiSessionChangedError' || error.name === 'ApiRequestTimeoutError' || error.message.includes('security token'))) throw error
    throw new Error('Could not reach the Bias Audit service. Check your connection and try again.')
  }

  let body: Partial<AuditResult> & { detail?: string | { msg?: string }[] }
  try {
    body = await response.json() as typeof body
  } catch {
    throw new Error('The audit service returned an unreadable response. Please try again.')
  }
  if (!response.ok) {
    if (response.status === 401) throw new Error('Your sign-in session has expired. Sign in again to run the audit.')
    const detail = typeof body.detail === 'string'
      ? body.detail
      : Array.isArray(body.detail)
        ? body.detail.map((item) => item.msg).filter((message): message is string => Boolean(message)).join(' ')
        : ''
    throw new Error(detail || 'The audit could not be completed. Check the fields and try again.')
  }
  if (!Array.isArray(body.findings) || typeof body.finding_count !== 'number' || typeof body.criteria_reviewed !== 'number' || typeof body.disclaimer !== 'string') {
    throw new Error('The audit service returned an unsupported response. Please try again.')
  }
  return body as AuditResult
}

async function requestSavedAudit<T>(path: string, init: RequestInit, fetcher: AuditFetcher, failure: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetcher(path, { ...init, signal })
  } catch (error) {
    if (error instanceof Error && ['AbortError', 'ApiSessionChangedError', 'ApiRequestTimeoutError'].includes(error.name)) throw error
    throw new Error('Could not reach the saved audit service. Check your connection and try again.')
  }
  const body = await response.json().catch(() => ({})) as T & { detail?: unknown }
  if (!response.ok) {
    if (response.status === 401) throw new Error('Your sign-in session has expired. Sign in again to continue.')
    const detail = typeof body.detail === 'string' ? body.detail : ''
    throw new Error(detail || failure)
  }
  return body
}

export function saveAudit(payload: AuditRequest, fetcher: AuditFetcher = skillFairFetch, signal?: AbortSignal): Promise<SavedAudit> {
  return requestSavedAudit('/api/jobs/audits', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, fetcher, 'The audit could not be saved. Please try again.', signal)
}

export function listSavedAudits(fetcher: AuditFetcher = skillFairFetch, signal?: AbortSignal): Promise<SavedAuditList> {
  return requestSavedAudit('/api/jobs/audits?limit=20', { method: 'GET' }, fetcher, 'Saved audit history could not be loaded.', signal)
}

export function getSavedAudit(auditId: string, fetcher: AuditFetcher = skillFairFetch, signal?: AbortSignal): Promise<SavedAudit> {
  return requestSavedAudit(`/api/jobs/audits/${encodeURIComponent(auditId)}`, { method: 'GET' }, fetcher, 'The saved audit could not be opened.', signal)
}

export function updateSavedAuditReview(auditId: string, findingIndex: number, reviewState: ReviewState, fetcher: AuditFetcher = skillFairFetch, signal?: AbortSignal): Promise<SavedAudit> {
  return requestSavedAudit(`/api/jobs/audits/${encodeURIComponent(auditId)}/findings/${findingIndex}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ review_state: reviewState }),
  }, fetcher, 'The review selection could not be saved.', signal)
}

export async function deleteSavedAudit(auditId: string, fetcher: AuditFetcher = skillFairFetch, signal?: AbortSignal): Promise<void> {
  let response: Response
  try {
    response = await fetcher(`/api/jobs/audits/${encodeURIComponent(auditId)}`, { method: 'DELETE', signal })
  } catch (error) {
    if (error instanceof Error && ['AbortError', 'ApiSessionChangedError', 'ApiRequestTimeoutError'].includes(error.name)) throw error
    throw new Error('Could not reach the saved audit service. Check your connection and try again.')
  }
  if (!response.ok) {
    if (response.status === 401) throw new Error('Your sign-in session has expired. Sign in again to continue.')
    throw new Error('The saved audit could not be deleted.')
  }
}
