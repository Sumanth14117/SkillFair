import { skillFairFetch } from './apiClient'

export type MarketDataSource = {
  source_id: string
  source_name: string
  source_type: string
  verification_status: 'verified'
  retrieved_at: string
  published_at: string | null
  geography: string | null
  source_reference: string
}

export type MarketInsight = {
  insight_id: string
  category: string
  title: string
  summary: string
  value: number | null
  unit: string | null
  period: string
  geography: string | null
  source_id: string
  calculation_method: string
  data_quality: 'high' | 'moderate' | 'limited'
}

export type MarketIntelligenceResult = {
  job_title: string
  geography: string | null
  data_availability: 'available' | 'unavailable'
  freshness: 'current' | 'stale' | 'not_available'
  sources: MarketDataSource[]
  insights: MarketInsight[]
  message: string
}

export type MarketCountryCode = 'gb' | 'us' | 'at' | 'au' | 'be' | 'br' | 'ca' | 'ch' | 'de' | 'es' | 'fr' | 'in' | 'it' | 'mx' | 'nl' | 'nz' | 'pl' | 'sg' | 'za'

export type MarketProviderStatus = {
  provider: string | null
  availability: 'available' | 'unavailable' | 'not_configured'
  checked_at: string | null
  message: string
}

export type MarketErrorKind = 'validation' | 'network' | 'service'

function getValidationMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string' ? item.msg : '')
      .filter(Boolean).join(' ')
  }
  return ''
}

export async function fetchMarketIntelligence(payload: { job_title: string; geography?: string; country_code: MarketCountryCode; skill_queries?: string[] }): Promise<MarketIntelligenceResult> {
  let response: Response
  try {
    response = await skillFairFetch('/api/market/intelligence', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : 'Could not reach Market Intelligence. Check your connection and try again.'
    throw Object.assign(new Error(message), { kind: 'network' as MarketErrorKind })
  }

  let body: Partial<MarketIntelligenceResult> & { detail?: unknown }
  try {
    body = await response.json() as typeof body
  } catch {
    throw Object.assign(new Error('The service returned an unreadable response. Please try again.'), { kind: 'service' as MarketErrorKind })
  }
  if (!response.ok) {
    const detail = getValidationMessage(body.detail)
    throw Object.assign(new Error(detail || 'Market Intelligence could not be loaded. Please try again.'), {
      kind: response.status === 422 ? 'validation' as MarketErrorKind : 'service' as MarketErrorKind,
    })
  }
  if (typeof body.job_title !== 'string' || !['available', 'unavailable'].includes(body.data_availability ?? '') || !['current', 'stale', 'not_available'].includes(body.freshness ?? '') || !Array.isArray(body.sources) || !Array.isArray(body.insights) || typeof body.message !== 'string') {
    throw Object.assign(new Error('The service returned an unsupported response. Please try again.'), { kind: 'service' as MarketErrorKind })
  }
  return body as MarketIntelligenceResult
}

export async function fetchProviderStatus(signal?: AbortSignal): Promise<MarketProviderStatus> {
  const response = await skillFairFetch('/api/market/provider-status', { method: 'GET', signal })
  let body: unknown
  try { body = await response.json() } catch {
    throw new Error('Market-data provider status could not be read.')
  }
  if (!response.ok || !body || typeof body !== 'object' || !('availability' in body) || !['available', 'unavailable', 'not_configured'].includes(String(body.availability)) || !('message' in body) || typeof body.message !== 'string') {
    throw new Error('Market-data provider status is unavailable.')
  }
  return body as MarketProviderStatus
}
