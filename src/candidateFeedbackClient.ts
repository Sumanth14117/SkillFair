import { skillFairFetch } from './apiClient'

export type CandidateSuggestionCategory = 'clarity' | 'accessibility' | 'communication' | 'other'
export type CandidateFeedbackPayload = {
  submission_id: string
  consent_to_aggregate: true
  application_experience: number
  process_accessibility: number
  job_requirements_clarity: number
  perceived_fairness: number
  suggestion_categories: CandidateSuggestionCategory[]
}
export type CandidateFeedbackResult = { accepted: true; duplicate: boolean; message: string }
type FeedbackFetcher = typeof skillFairFetch

export async function submitCandidateFeedback(
  payload: CandidateFeedbackPayload,
  fetcher: FeedbackFetcher = skillFairFetch,
  signal?: AbortSignal,
): Promise<CandidateFeedbackResult> {
  let response: Response
  try {
    response = await fetcher('/api/impact/feedback/candidate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    })
  } catch (error) {
    if (error instanceof Error && ['AbortError', 'ApiSessionChangedError', 'ApiRequestTimeoutError'].includes(error.name)) throw error
    throw new Error('The candidate feedback service could not be reached. Check your connection and try again.')
  }
  const body = await response.json().catch(() => ({})) as Partial<CandidateFeedbackResult> & { detail?: unknown }
  if (!response.ok) {
    if (response.status === 401) throw new Error('Your sign-in session has expired. Sign in again to continue.')
    if (response.status === 422) throw new Error('Check the ratings and selected suggestions, then try again.')
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Candidate feedback could not be submitted.')
  }
  if (body.accepted !== true || typeof body.duplicate !== 'boolean' || typeof body.message !== 'string') {
    throw new Error('The feedback service returned an unsupported response.')
  }
  return body as CandidateFeedbackResult
}
