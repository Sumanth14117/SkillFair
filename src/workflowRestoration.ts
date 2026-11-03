import { skillFairFetch } from './apiClient'
import { verifyRun, type CareerWorkflowRun } from './components/CareerOrchestrator'
import { isValidSessionLocator } from './sessionState'

export type WorkflowRestoreResult =
  | { kind: 'none' }
  | { kind: 'missing' }
  | { kind: 'restored'; run: CareerWorkflowRun }

export async function restoreSavedWorkflow(
  runId: string | null,
  fetcher: typeof skillFairFetch = skillFairFetch,
  signal?: AbortSignal,
): Promise<WorkflowRestoreResult> {
  if (runId === null) return { kind: 'none' }
  if (!isValidSessionLocator(runId)) return { kind: 'missing' }

  const response = await fetcher(`/api/career/orchestrator/runs/${encodeURIComponent(runId)}`, { signal })
  if (response.status === 404) return { kind: 'missing' }
  if (!response.ok) throw new Error(response.status === 401
    ? 'Your sign-in session has expired. Sign in again.'
    : 'The saved workflow could not be restored. Retry when the service is available.')

  let body: unknown
  try { body = await response.json() } catch {
    throw new Error('The workflow service returned an unreadable response.')
  }
  if (!body || typeof body !== 'object' || !('run_id' in body) || body.run_id !== runId) {
    throw new Error('The workflow service returned a mismatched workflow reference.')
  }
  return { kind: 'restored', run: verifyRun(body as CareerWorkflowRun) }
}
