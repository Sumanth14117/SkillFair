import { skillFairFetch } from './apiClient'
import { isValidSessionLocator } from './sessionState'

export type WorkflowHistoryStatus = 'awaiting_human_review' | 'revise_required' | 'stopped' | 'complete'
export type WorkflowHistoryItem = {
  run_id: string
  job_title: string
  job_description: string
  geography: string | null
  essential_skills: string[]
  preferred_skills: string[]
  created_at: string
  updated_at: string
  workflow_status: WorkflowHistoryStatus
}

export type WorkflowHistoryState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: WorkflowHistoryItem[] }

type HistoryFetcher = typeof skillFairFetch

function validDate(value: unknown): value is string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
}

function parseHistoryItem(value: unknown): WorkflowHistoryItem | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  const workflow = item.workflow
  if (!workflow || typeof workflow !== 'object') return null
  const status = (workflow as Record<string, unknown>).workflow_status
  if (!isValidSessionLocator(item.run_id)
    || typeof item.job_title !== 'string'
    || typeof item.job_description !== 'string'
    || (item.geography !== null && typeof item.geography !== 'string')
    || !Array.isArray(item.essential_skills) || !item.essential_skills.every((skill) => typeof skill === 'string')
    || !Array.isArray(item.preferred_skills) || !item.preferred_skills.every((skill) => typeof skill === 'string')
    || !validDate(item.created_at) || !validDate(item.updated_at)
    || !['awaiting_human_review', 'revise_required', 'stopped', 'complete'].includes(String(status))) return null
  return {
    run_id: item.run_id,
    job_title: item.job_title,
    job_description: item.job_description,
    geography: item.geography,
    essential_skills: item.essential_skills,
    preferred_skills: item.preferred_skills,
    created_at: item.created_at,
    updated_at: item.updated_at,
    workflow_status: status as WorkflowHistoryStatus,
  }
}

/** Fetches only the signed-in account's server-owned history; stale owners are ignored. */
export async function loadWorkflowHistory(
  ownerId: string,
  getCurrentOwnerId: () => string,
  fetcher: HistoryFetcher = skillFairFetch,
  signal?: AbortSignal,
): Promise<WorkflowHistoryState | null> {
  const stale = () => signal?.aborted === true || getCurrentOwnerId() !== ownerId
  if (stale()) return null
  const response = await fetcher('/api/career/orchestrator/runs?limit=20', { signal })
  if (stale()) return null
  if (!response.ok) {
    throw new Error(response.status === 401
      ? 'Your sign-in session has expired. Sign in again to view saved workflows.'
      : 'Saved workflows could not be loaded. Retry when the service is available.')
  }
  let payload: unknown
  try { payload = await response.json() } catch {
    throw new Error('The workflow history response could not be read. Please retry.')
  }
  if (stale()) return null
  if (!Array.isArray(payload)) throw new Error('The workflow history response had an unsupported format.')
  const items = payload.map(parseHistoryItem)
  if (items.some((item) => item === null)) throw new Error('The workflow history response had an unsupported format.')
  return { status: 'ready', items: items as WorkflowHistoryItem[] }
}
