import type { DashboardOverview } from './types/dashboard'

export type DashboardLoadState =
  | { status: 'loading'; ownerId: string }
  | { status: 'error'; ownerId: string; message: string }
  | { status: 'ready'; ownerId: string; data: DashboardOverview }

export function dashboardStateForOwner(state: DashboardLoadState, ownerId: string): DashboardLoadState {
  return state.ownerId === ownerId ? state : { status: 'loading', ownerId }
}

type DashboardFetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && (value as number) >= 0
}

function isTimestamp(value: unknown): value is string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
}

export function isDashboardOverview(value: unknown): value is DashboardOverview {
  if (!isRecord(value) || !isTimestamp(value.generated_at) || !isRecord(value.summary)) return false
  const summary = value.summary
  if (!isNonNegativeInteger(summary.saved_analyses)
    || !isNonNegativeInteger(summary.total_workflows)
    || !isNonNegativeInteger(summary.pending_reviews)
    || !isNonNegativeInteger(summary.completed_reviews)) return false
  if (!Array.isArray(value.workflow_status_distribution) || !Array.isArray(value.review_decision_distribution)
    || !Array.isArray(value.recent_workflows) || !Array.isArray(value.recent_activity)) return false

  const workflowStatuses = ['awaiting_human_review', 'revise_required', 'stopped', 'complete']
  const decisions = ['pending', 'proceed', 'revise', 'stop']
  if (!value.workflow_status_distribution.every((entry) => isRecord(entry)
    && workflowStatuses.includes(String(entry.status)) && isNonNegativeInteger(entry.count))) return false
  if (!value.review_decision_distribution.every((entry) => isRecord(entry)
    && decisions.includes(String(entry.decision)) && isNonNegativeInteger(entry.count))) return false
  if (!value.recent_workflows.every((entry) => isRecord(entry)
    && typeof entry.run_id === 'string' && typeof entry.job_title === 'string'
    && workflowStatuses.includes(String(entry.workflow_status)) && decisions.includes(String(entry.review_decision))
    && isTimestamp(entry.created_at) && isTimestamp(entry.updated_at))) return false
  return value.recent_activity.every((entry) => isRecord(entry)
    && ['workflow_created', 'review_started', 'review_decided'].includes(String(entry.event_type))
    && typeof entry.workflow_id === 'string' && typeof entry.job_title === 'string'
    && typeof entry.description === 'string' && isTimestamp(entry.occurred_at)
    && workflowStatuses.includes(String(entry.workflow_status))
    && (entry.decision === null || ['proceed', 'revise', 'stop'].includes(String(entry.decision))))
}

export async function loadDashboardOverview(
  ownerId: string,
  getCurrentOwner: () => string,
  fetcher: DashboardFetcher,
  signal?: AbortSignal,
): Promise<DashboardOverview | null> {
  const response = await fetcher('/api/dashboard/overview', { method: 'GET', signal })
  if (getCurrentOwner() !== ownerId) return null
  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new Error('Overview data could not be read. Please retry.')
  }
  if (getCurrentOwner() !== ownerId) return null
  if (response.status === 401) throw new Error('Your session has expired. Sign in again to view this account dashboard.')
  if (!response.ok) throw new Error('Overview data is temporarily unavailable. Please retry.')
  if (!isDashboardOverview(body)) throw new Error('Overview data had an unsupported format. Please retry.')
  return body
}
