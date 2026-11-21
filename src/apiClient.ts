import { csrfTokenFromCookie } from './authClient'
import { currentSessionEpoch, notifySessionExpired, subscribeSessionEpoch } from './sessionExpiry'

export const API_TIMEOUT_MS = 90_000

export class ApiRequestTimeoutError extends Error {
  constructor() {
    super('SkillFair is taking longer to respond than expected and may be waking from idle. The request was not automatically retried; check whether an action completed before submitting it again.')
    this.name = 'ApiRequestTimeoutError'
  }
}

export class ApiSessionChangedError extends Error {
  constructor() {
    super('The authenticated account changed while this request was in progress.')
    this.name = 'ApiSessionChangedError'
  }
}

function isLoginOrRegistrationRequest(input: RequestInfo | URL): boolean {
  const rawUrl = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  let pathname: string
  try {
    pathname = new URL(rawUrl, typeof location === 'undefined' ? 'http://localhost' : location.origin).pathname
  } catch {
    return false
  }
  return pathname === '/api/auth/login' || pathname === '/api/auth/register'
}

function responseWithBoundedBody(
  response: Response,
  signal: AbortSignal,
  didTimeOut: () => boolean,
  cleanup: () => void,
): Response {
  if (!response.body) {
    cleanup()
    return response
  }

  const reader = response.body.getReader()
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
  let finished = false
  let streamFailure: unknown = null
  const finish = () => {
    if (finished) return
    finished = true
    signal.removeEventListener('abort', onAbort)
    cleanup()
  }
  const fail = (error: unknown) => {
    if (finished) return
    streamFailure = didTimeOut() ? new ApiRequestTimeoutError() : error
    try { streamController?.error(streamFailure) } catch { /* Stream may already be closed. */ }
    void reader.cancel(error).catch(() => undefined)
    finish()
  }
  const onAbort = () => fail(signal.reason ?? new DOMException('The request was cancelled.', 'AbortError'))
  signal.addEventListener('abort', onAbort, { once: true })
  if (signal.aborted) onAbort()

  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller
      if (streamFailure !== null) controller.error(streamFailure)
    },
    pull(controller) {
      if (finished) return
      return reader.read().then(({ done, value }) => {
        if (finished) return
        if (done) {
          controller.close()
          finish()
        } else {
          controller.enqueue(value)
        }
      }).catch((error: unknown) => fail(error))
    },
    cancel(reason) {
      finish()
      return reader.cancel(reason)
    },
  })

  return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers })
}

export async function skillFairFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const requestEpoch = currentSessionEpoch()
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !headers.has('X-CSRF-Token')) {
    const token = csrfTokenFromCookie()
    if (!token) throw new Error('The security token is missing. Refresh the page and try again.')
    headers.set('X-CSRF-Token', token)
  }

  const controller = new AbortController()
  const callerSignal = init.signal
  let timedOut = false
  let sessionChanged = false
  let cleanedUp = false
  let unsubscribeEpoch: () => void = () => undefined
  const cleanup = () => {
    if (cleanedUp) return
    cleanedUp = true
    globalThis.clearTimeout(timeout)
    callerSignal?.removeEventListener('abort', abortFromCaller)
    unsubscribeEpoch()
  }
  const timeout = globalThis.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, API_TIMEOUT_MS)
  const abortFromCaller = () => controller.abort(callerSignal?.reason)
  unsubscribeEpoch = subscribeSessionEpoch((epoch) => {
    if (epoch !== requestEpoch) {
      sessionChanged = true
      controller.abort(new ApiSessionChangedError())
    }
  })
  if (currentSessionEpoch() !== requestEpoch) {
    sessionChanged = true
    controller.abort(new ApiSessionChangedError())
  }
  if (callerSignal?.aborted) abortFromCaller()
  else callerSignal?.addEventListener('abort', abortFromCaller, { once: true })
  try {
    const response = await fetch(input, { ...init, headers, credentials: 'include', signal: controller.signal })
    if (requestEpoch !== currentSessionEpoch()) throw new ApiSessionChangedError()
    if (response.status === 401 && !isLoginOrRegistrationRequest(input)) notifySessionExpired(requestEpoch)
    return responseWithBoundedBody(response, controller.signal, () => timedOut, cleanup)
  } catch (error) {
    cleanup()
    if (timedOut) throw new ApiRequestTimeoutError()
    if (sessionChanged) throw new ApiSessionChangedError()
    throw error
  }
}
