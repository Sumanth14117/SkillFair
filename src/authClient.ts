export type AuthUser = {
  id: string
  email: string
  created_at: string
}

export function authDisplayName(user: AuthUser): string {
  const localPart = user.email.split('@', 1)[0] || user.email
  return localPart.replace(/[._-]+/g, ' ').replace(/\b\p{L}/gu, (letter) => letter.toUpperCase())
}

export class AuthApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'AuthApiError'
    this.status = status
  }
}

const SAFE_MESSAGES: Record<number, string> = {
  401: 'Your session is no longer valid. Sign in again.',
  403: 'This request could not be verified. Refresh the page and try again.',
  409: 'Unable to create the account. Check the details or sign in.',
  429: 'Too many authentication attempts. Wait a moment and try again.',
}
const AUTH_REQUEST_TIMEOUT_MS = 90_000
const REQUEST_TIMEOUT_MESSAGE = 'SkillFair is taking longer to respond than expected and may be waking from idle. The request was not automatically retried; check whether an action completed before submitting it again.'
const SAFE_BACKEND_DETAILS = new Set([
  'Invalid email or password.',
  'Unable to create account. Check the details or sign in.',
  'Authentication required.',
  'Request origin is not allowed.',
  'CSRF validation failed.',
  'Enter a valid email address.',
  'Request validation failed.',
  'Authentication cookie security is misconfigured.',
])

async function responseError(response: Response): Promise<AuthApiError> {
  let message = SAFE_MESSAGES[response.status] ?? 'Authentication is temporarily unavailable. Please try again.'
  try {
    const payload: unknown = await response.json()
    if (payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string' && SAFE_BACKEND_DETAILS.has(payload.detail)) {
      message = payload.detail
    }
  } catch {
    // Keep the safe status-specific message when the response is not JSON.
  }
  return new AuthApiError(message, response.status)
}

async function requestWithTimeout<T>(
  path: string,
  init: RequestInit,
  fetcher: typeof fetch,
  consume: (response: Response) => Promise<T>,
): Promise<T> {
  const controller = new AbortController()
  const timeoutId = globalThis.setTimeout(() => controller.abort(), AUTH_REQUEST_TIMEOUT_MS)
  try {
    const response = await fetcher(path, { ...init, signal: controller.signal, credentials: 'include' })
    return await consume(response)
  } catch (error) {
    if (controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
      throw new AuthApiError(REQUEST_TIMEOUT_MESSAGE, 0)
    }
    if (error instanceof AuthApiError) throw error
    throw new AuthApiError('Could not reach SkillFair. Check your connection and try again.', 0)
  } finally {
    globalThis.clearTimeout(timeoutId)
  }
}

async function requestUser(path: string, init: RequestInit = {}, fetcher: typeof fetch = fetch): Promise<AuthUser> {
  try {
    return await requestWithTimeout(path, init, fetcher, async (response) => {
      if (!response.ok) throw await responseError(response)
      try {
        const user: unknown = await response.json()
        if (!user || typeof user !== 'object' || !('id' in user) || !('email' in user) || !('created_at' in user)
          || typeof user.id !== 'string' || typeof user.email !== 'string' || typeof user.created_at !== 'string') {
          throw new Error('Invalid response')
        }
        return { id: user.id, email: user.email, created_at: user.created_at }
      } catch {
        throw new AuthApiError('The authentication service returned an unexpected response.', response.status)
      }
    })
  } catch (error) {
    if (error instanceof AuthApiError) throw error
    throw new AuthApiError('Could not reach SkillFair. Check your connection and try again.', 0)
  }
}

function jsonRequest(email: string, password: string): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  }
}

const currentUserRequests = new WeakMap<typeof fetch, Promise<AuthUser>>()

export function getCurrentUser(fetcher: typeof fetch = fetch, force = false): Promise<AuthUser> {
  const existing = currentUserRequests.get(fetcher)
  if (existing && !force) return existing
  const pending = requestUser('/api/auth/me', { method: 'GET' }, fetcher).finally(() => {
    if (currentUserRequests.get(fetcher) === pending) currentUserRequests.delete(fetcher)
  })
  currentUserRequests.set(fetcher, pending)
  return pending
}

export function registerAccount(email: string, password: string, fetcher: typeof fetch = fetch): Promise<void> {
  return requestWithTimeout('/api/auth/register', jsonRequest(email, password), fetcher, async (response) => {
    if (!response.ok) throw await responseError(response)
    let payload: unknown
    try { payload = await response.json() } catch {
      throw new AuthApiError('The authentication service returned an unexpected response.', response.status)
    }
    if (response.status !== 202 || !payload || typeof payload !== 'object' || !('message' in payload)
      || payload.message !== 'Registration request processed. If the address already has an account, sign in. No verification email is sent.') {
      throw new AuthApiError('The authentication service returned an unexpected response.', response.status)
    }
  })
}

export function loginAccount(email: string, password: string, fetcher: typeof fetch = fetch): Promise<AuthUser> {
  return requestUser('/api/auth/login', jsonRequest(email, password), fetcher)
}

export function csrfTokenFromCookie(cookieString?: string): string | null {
  const source = cookieString ?? (typeof document === 'undefined' ? '' : document.cookie)
  const item = source.split(';').map((value) => value.trim()).find((value) => value.startsWith('skillfair_csrf='))
  if (!item) return null
  try {
    return decodeURIComponent(item.slice('skillfair_csrf='.length)) || null
  } catch {
    return null
  }
}

export async function logoutAccount(fetcher: typeof fetch = fetch, cookieString?: string): Promise<void> {
  const csrfToken = csrfTokenFromCookie(cookieString)
  if (!csrfToken) throw new AuthApiError('The security token is missing. Refresh the page and try again.', 403)

  try {
    await requestWithTimeout('/api/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
    }, fetcher, async (response) => {
      if (!response.ok) throw await responseError(response)
    })
  } catch (error) {
    if (error instanceof AuthApiError) throw error
    throw new AuthApiError('Could not reach SkillFair. Check your connection and try again.', 0)
  }
}
