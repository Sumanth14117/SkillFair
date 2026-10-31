import type { CareerWorkflowRun } from './components/CareerOrchestrator'

export const RECRUITER_REVIEW_WORKSPACE_ID = 'recruiter-review-workspace'

export type ReviewRestoreState = 'idle' | 'loading' | 'error'
export type RecruiterReviewViewState = 'empty' | 'loading' | 'error' | 'workflow'

export function getRecruiterReviewViewState(
  run: CareerWorkflowRun | null,
  runId: string | null,
  restoreState: ReviewRestoreState,
): RecruiterReviewViewState {
  if (restoreState === 'loading' || (runId !== null && run?.run_id !== runId)) return 'loading'
  if (restoreState === 'error') return 'error'
  return run ? 'workflow' : 'empty'
}
