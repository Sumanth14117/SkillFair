import { skillFairFetch } from './apiClient'

export type SearchPage = 'Skills Discovery' | 'Career Orchestrator' | 'Bias Audit'
export type SearchResult = {
  record_type: 'analysis' | 'workflow' | 'bias_audit'
  record_id: string
  title: string
  detail: string
  page: SearchPage
  updated_at: string
}
export type AccountSearchResponse = { query: string; results: SearchResult[] }
export type NotificationItem = {
  notification_id: string
  notification_type: 'workflow_decision' | 'learning_pathways_ready'
  title: string
  message: string
  created_at: string
  read_at: string | null
  workflow_id: string | null
}
export type NotificationList = { notifications: NotificationItem[]; unread_count: number }

async function safeJson<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => ({})) as T & { detail?: unknown }
  if (!response.ok) {
    if (response.status === 401) throw new Error('Your sign-in session has expired. Sign in again.')
    throw new Error(typeof body.detail === 'string' ? body.detail : fallback)
  }
  return body
}

export async function searchAccount(query: string, signal?: AbortSignal): Promise<AccountSearchResponse> {
  const response = await skillFairFetch(`/api/search?q=${encodeURIComponent(query)}`, { method: 'GET', signal })
  return safeJson<AccountSearchResponse>(response, 'Search is temporarily unavailable.')
}

export async function listNotifications(signal?: AbortSignal): Promise<NotificationList> {
  const response = await skillFairFetch('/api/notifications?limit=20', { method: 'GET', signal })
  return safeJson<NotificationList>(response, 'Notifications are temporarily unavailable.')
}

export async function markNotificationRead(notificationId: string, signal?: AbortSignal): Promise<NotificationItem> {
  const response = await skillFairFetch(`/api/notifications/${encodeURIComponent(notificationId)}/read`, {
    method: 'POST', signal,
  })
  return safeJson<NotificationItem>(response, 'The notification could not be updated.')
}
