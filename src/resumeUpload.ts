import { API_TIMEOUT_MS, ApiSessionChangedError } from './apiClient'
import { currentSessionEpoch, notifySessionExpired, subscribeSessionEpoch } from './sessionExpiry'
import { csrfTokenFromCookie } from './authClient'
import type { SkillsDiscoveryResult } from './types/skills'

export class ResumeUploadTimeoutError extends Error {
  constructor() {
    super('Resume analysis timed out. The request may have completed; check your saved Skills Discovery results before submitting it again.')
    this.name = 'ResumeUploadTimeoutError'
  }
}

export type UploadXhr = XMLHttpRequest
export type UploadXhrFactory = () => UploadXhr

export function uploadResume(
  file: File,
  analysisMode: 'local' | 'ai',
  consentToAIProvider: boolean,
  consentedProvider: 'gemini' | 'groq' | undefined,
  onProgress: (value: number) => void,
  signal?: AbortSignal,
  options: { xhrFactory?: UploadXhrFactory; csrfToken?: () => string | null; timeoutMs?: number } = {},
): Promise<SkillsDiscoveryResult> {
  return new Promise((resolve, reject) => {
    const csrfToken = (options.csrfToken ?? csrfTokenFromCookie)()
    if (!csrfToken) {
      reject(new Error('The security token is missing. Refresh the page and try again.'))
      return
    }
    const xhr = (options.xhrFactory ?? (() => new XMLHttpRequest()))()
    const epoch = currentSessionEpoch()
    let settled = false
    let unsubscribeEpoch: () => void = () => undefined
    const cleanup = () => {
      signal?.removeEventListener('abort', abortFromCaller)
      unsubscribeEpoch()
      xhr.upload.onprogress = null
      xhr.upload.onload = null
      xhr.onerror = null
      xhr.ontimeout = null
      xhr.onabort = null
      xhr.onload = null
    }
    const finish = (error?: Error, result?: SkillsDiscoveryResult) => {
      if (settled) return
      settled = true
      cleanup()
      if (error) reject(error)
      else if (result) resolve(result)
      else reject(new Error('The service did not return an analysis result. Please retry Skills Discovery.'))
    }
    const abortRequest = (error: Error) => {
      finish(error)
      try { xhr.abort() } catch { /* Request may already have completed. */ }
    }
    const abortFromCaller = () => {
      const reason = signal?.reason
      abortRequest(reason instanceof ApiSessionChangedError
        ? reason
        : reason instanceof Error && reason.name === 'AbortError'
          ? reason
          : new DOMException('The resume upload was cancelled.', 'AbortError'))
    }
    try {
      xhr.open('POST', '/api/skills/discover')
      xhr.withCredentials = true
      xhr.timeout = options.timeoutMs ?? API_TIMEOUT_MS
      xhr.setRequestHeader('X-CSRF-Token', csrfToken)
      xhr.upload.onprogress = (event) => {
        if (!settled && event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
      }
      xhr.upload.onload = () => { if (!settled) onProgress(100) }
      xhr.onerror = () => finish(new Error('Could not reach the Skills Discovery service. Check your connection and try again.'))
      xhr.ontimeout = () => finish(new ResumeUploadTimeoutError())
      xhr.onabort = () => finish(signal?.reason instanceof ApiSessionChangedError
        ? signal.reason
        : new DOMException('The resume upload was cancelled.', 'AbortError'))
      xhr.onload = () => {
        if (epoch !== currentSessionEpoch()) {
          finish(new ApiSessionChangedError())
          return
        }
        let body: { detail?: string } & Partial<SkillsDiscoveryResult>
        try { body = JSON.parse(xhr.responseText) as typeof body } catch {
          finish(new Error('The service returned an unreadable response.'))
          return
        }
        if (xhr.status === 401) {
          notifySessionExpired(epoch)
          finish(new Error('Your sign-in session has expired. Sign in again before uploading a resume.'))
          return
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          finish(new Error(body.detail ?? 'Resume analysis failed. Please try again.'))
          return
        }
        if (typeof body.analysis_id !== 'string' || !body.analysis_id) {
          finish(new Error('The service did not return an analysis reference. Please retry Skills Discovery.'))
          return
        }
        finish(undefined, body as SkillsDiscoveryResult)
      }
      unsubscribeEpoch = subscribeSessionEpoch((currentEpoch) => {
        if (currentEpoch !== epoch) abortRequest(new ApiSessionChangedError())
      })
      if (currentSessionEpoch() !== epoch) {
        abortRequest(new ApiSessionChangedError())
        return
      }
      if (signal?.aborted) {
        abortFromCaller()
        return
      }
      signal?.addEventListener('abort', abortFromCaller, { once: true })
      const form = new FormData()
      form.append('file', file)
      form.append('analysis_mode', analysisMode)
      form.append('consent_to_ai_provider', String(consentToAIProvider))
      if (consentToAIProvider && consentedProvider) form.append('consented_ai_provider', consentedProvider)
      xhr.send(form)
    } catch {
      finish(new Error('Could not start the resume upload. Please retry Skills Discovery.'))
    }
  })
}
