import { skillFairFetch } from './apiClient'

export type AccountAnalysis = {
  analysis_id: string
  created_at: string
  analysis_mode: 'development' | 'local' | 'ai'
  skill_count: number
  linked_workflow_count: number
  status: 'available' | 'expired' | 'deleted'
}

export type AccountWorkflow = {
  run_id: string
  job_title: string
  created_at: string
  updated_at: string
  workflow_status: 'awaiting_human_review' | 'revise_required' | 'stopped' | 'complete'
  review_decision: 'proceed' | 'revise' | 'stop' | null
  status: 'available' | 'expired'
}

export type AccountData = {
  analysis_total: number
  workflow_total: number
  limit: number
  offset: number
  analyses: AccountAnalysis[]
  workflows: AccountWorkflow[]
}

export type AccountDataState =
  | { ownerId: string; status: 'loading' }
  | { ownerId: string; status: 'error'; message: string }
  | { ownerId: string; status: 'ready'; data: AccountData }

export function accountDataLoading(ownerId: string): AccountDataState {
  return { ownerId, status: 'loading' }
}

export function accountDataError(ownerId: string, message: string): AccountDataState {
  return { ownerId, status: 'error', message }
}

export function accountDataReady(ownerId: string, data: AccountData): AccountDataState {
  return { ownerId, status: 'ready', data }
}

export function visibleAccountData(state: AccountDataState, authenticatedOwnerId: string): AccountData | null {
  return state.ownerId === authenticatedOwnerId && state.status === 'ready' ? state.data : null
}

async function safeError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = await response.json() as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail.length <= 300) return new Error(body.detail)
  } catch { /* Use the stable local message for a non-JSON response. */ }
  return new Error(fallback)
}

export async function loadAccountData(ownerId: string, offset: number, signal: AbortSignal, request = skillFairFetch): Promise<AccountDataState> {
  const response = await request(`/api/account/data?limit=25&offset=${offset}`, { signal })
  if (!response.ok) throw await safeError(response, 'Account data could not be loaded. Please retry.')
  const body = await response.json() as AccountData
  if (!Number.isInteger(body.analysis_total) || !Number.isInteger(body.workflow_total)
    || !Array.isArray(body.analyses) || !Array.isArray(body.workflows)) {
    throw new Error('The account data response had an unsupported format.')
  }
  return accountDataReady(ownerId, body)
}

export async function downloadAccountExport(request = skillFairFetch): Promise<{ blob: Blob; filename: string }> {
  const response = await request('/api/account/export')
  if (!response.ok) throw await safeError(response, 'Your account export could not be prepared. Please retry.')
  const blob = await response.blob()
  return { blob, filename: 'skillfair-account-export.json' }
}

export async function confirmAndDeleteAccountRecord(
  kind: 'analysis' | 'workflow',
  id: string,
  confirm: (message: string) => boolean,
  request = skillFairFetch,
): Promise<boolean> {
  const prompt = kind === 'analysis'
    ? 'Delete this saved analysis and its stored skill evidence? Any linked workflows must be deleted first. This cannot be undone.'
    : 'Delete this workflow and its review, linked feedback, and workflow review measurements? This cannot be undone.'
  if (!confirm(prompt)) return false
  const segment = kind === 'analysis' ? 'analyses' : 'workflows'
  const response = await request(`/api/account/${segment}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!response.ok) throw await safeError(response, `The ${kind} could not be deleted. Please retry.`)
  return true
}
