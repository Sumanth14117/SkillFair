import { csrfTokenFromCookie } from './authClient'

const API_TIMEOUT_MS = 20_000

export async function skillFairFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !headers.has('X-CSRF-Token')) {
    const token = csrfTokenFromCookie()
    if (!token) throw new Error('The security token is missing. Refresh the page and try again.')
    headers.set('X-CSRF-Token', token)
  }

  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), API_TIMEOUT_MS)
  try {
    return await fetch(input, { ...init, headers, credentials: 'include', signal: init.signal ?? controller.signal })
  } finally {
    globalThis.clearTimeout(timeout)
  }
}
