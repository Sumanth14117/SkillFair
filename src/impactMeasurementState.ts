export type ImpactCategory = 'all' | 'operations' | 'recruiter_feedback' | 'candidate_feedback' | 'baseline'
export type ImpactAvailability = 'awaiting_data' | 'insufficient_sample' | 'measured' | 'unavailable'
export type ImpactMetricUnit = 'seconds' | 'count' | 'percent' | 'rating_1_to_5'
export type ImpactSummaryFilters = { category: ImpactCategory; startDate: string; endDate: string }

export function impactSummaryUrl(filters: ImpactSummaryFilters): string {
  const query = new URLSearchParams({ category: filters.category })
  if (filters.startDate) query.set('start_date', filters.startDate)
  if (filters.endDate) query.set('end_date', filters.endDate)
  return `/api/impact/summary?${query.toString()}`
}

export async function refreshImpactSummaryAfterSubmission(
  submissionSucceeded: boolean,
  reload: () => Promise<unknown>,
): Promise<void> {
  if (submissionSucceeded) await reload()
}

export function impactMetricValue(value: number | null, unit: ImpactMetricUnit, availability: ImpactAvailability): string {
  if (value === null) {
    if (availability === 'insufficient_sample') return 'Insufficient sample'
    if (availability === 'unavailable') return 'Unavailable'
    return 'Awaiting data'
  }
  if (unit === 'seconds') return value < 60 ? `${Math.round(value)} sec` : `${(value / 60).toFixed(1)} min`
  if (unit === 'percent') return `${value.toFixed(1)}%`
  if (unit === 'rating_1_to_5') return `${value.toFixed(2)} / 5`
  return String(value)
}

export function isAwaitingImpactObservations(metrics: Array<{ sample_size: number; data_availability: ImpactAvailability }>): boolean {
  return metrics.length === 0 || metrics.every((metric) => metric.sample_size === 0 && metric.data_availability === 'awaiting_data')
}

export function impactSummaryBelongsTo(summaryOwnerId: string, accountId: string): boolean {
  return Boolean(accountId) && summaryOwnerId === accountId
}

export function impactRequestErrorMessage(status: number, detail: unknown, fallback: string): string {
  if (status === 401) return 'Your sign-in session has expired. Sign in again to view account-scoped measurements.'
  if (typeof detail === 'string' && detail.length < 240) return detail
  return fallback
}

export type ImpactRequestToken = { sequence: number; accountId: string }

/** Prevents a response started for an earlier filter/account state from being applied. */
export class ImpactRequestTracker {
  private sequence = 0

  begin(accountId: string): ImpactRequestToken {
    this.sequence += 1
    return { sequence: this.sequence, accountId }
  }

  invalidate(): void {
    this.sequence += 1
  }

  isCurrent(token: ImpactRequestToken, accountId: string): boolean {
    return token.sequence === this.sequence && token.accountId === accountId
  }
}
