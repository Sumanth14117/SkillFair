import { expect, type BrowserContext, type Page, type Route } from '@playwright/test'

export const E2E_ORIGIN = 'http://127.0.0.1:4174'
export const REGISTRATION_MESSAGE = 'Registration request processed. If the address already has an account, sign in. No verification email is sent.'

export type SyntheticAccount = {
  id: string
  email: string
  password: string
  created_at: string
}

export const accountA: SyntheticAccount = {
  id: 'e2e-account-a',
  email: 'account.a@example.test',
  password: 'synthetic-account-a-password',
  created_at: '2026-11-20T08:00:00Z',
}

export const accountB: SyntheticAccount = {
  id: 'e2e-account-b',
  email: 'account.b@example.test',
  password: 'synthetic-account-b-password',
  created_at: '2026-11-20T08:05:00Z',
}

type MockOptions = {
  accounts?: SyntheticAccount[]
  registrationFailure?: { status: number; body: unknown }
  loginFailure?: { status: number; body: unknown }
  analysisByUserId?: Record<string, unknown>
  protectedUnauthorizedPath?: string
}

type PauseGate = {
  path: string
  userId?: string
  started: Promise<void>
  markStarted: () => void
  wait: Promise<void>
  release: () => void
}

type MockRequest = {
  method: string
  path: string
  origin: string | undefined
  hasSessionCookie: boolean
  csrfHeaderMatchesCookie?: boolean
  userId?: string
  status?: number
}

export type AuthApiMock = {
  requests: MockRequest[]
  pauseNextRequest: (path: string, userId?: string) => { started: Promise<void>; release: () => void }
  dispose: () => Promise<void>
}

function cookieValue(cookieHeader: string | undefined, name: string): string | null {
  const prefix = `${name}=`
  const item = cookieHeader?.split(';').map((part) => part.trim()).find((part) => part.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : null
}

async function json(route: Route, status: number, body: unknown): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

export async function installAuthApiMock(context: BrowserContext, options: MockOptions = {}): Promise<AuthApiMock> {
  const accounts = options.accounts ?? [accountA, accountB]
  const requests: MockRequest[] = []
  const sessions = new Map<string, { user: SyntheticAccount; revoked: boolean }>()
  const pauses: PauseGate[] = []
  let sessionNumber = 0

  const pauseNextRequest: AuthApiMock['pauseNextRequest'] = (path, userId) => {
    let markStarted!: () => void
    let release!: () => void
    const started = new Promise<void>((resolve) => { markStarted = resolve })
    const wait = new Promise<void>((resolve) => { release = resolve })
    pauses.push({ path, userId, started, markStarted, wait, release })
    return { started, release }
  }

  const handler = async (route: Route): Promise<void> => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const headers = request.headers()
    const sessionToken = cookieValue(headers.cookie, 'skillfair_session')
    const csrfCookie = cookieValue(headers.cookie, 'skillfair_csrf')
    const session = sessionToken ? sessions.get(sessionToken) : undefined
    const user = session && !session.revoked ? session.user : undefined
    const recorded: MockRequest = {
      method,
      path,
      origin: headers.origin,
      hasSessionCookie: Boolean(sessionToken),
      userId: user?.id,
    }
    requests.push(recorded)

    const pauseIndex = pauses.findIndex((pause) => pause.path === path && (!pause.userId || pause.userId === user?.id))
    if (pauseIndex >= 0) {
      const [pause] = pauses.splice(pauseIndex, 1)
      pause.markStarted()
      await pause.wait
    }

    if (path === '/api/auth/me' && method === 'GET') {
      if (!user) {
        recorded.status = 401
        await json(route, 401, { detail: 'Authentication required.' })
      } else {
        recorded.status = 200
        const publicUser = { id: user.id, email: user.email, created_at: user.created_at }
        await json(route, 200, publicUser)
      }
      return
    }

    if (path === '/api/auth/register' && method === 'POST') {
      if (headers.origin !== E2E_ORIGIN) {
        recorded.status = 403
        await json(route, 403, { detail: 'Request origin is not allowed.' })
      } else if (options.registrationFailure) {
        recorded.status = options.registrationFailure.status
        await json(route, options.registrationFailure.status, options.registrationFailure.body)
      } else {
        recorded.status = 202
        await json(route, 202, { message: REGISTRATION_MESSAGE })
      }
      return
    }

    if (path === '/api/auth/login' && method === 'POST') {
      if (headers.origin !== E2E_ORIGIN) {
        recorded.status = 403
        await json(route, 403, { detail: 'Request origin is not allowed.' })
        return
      }
      if (options.loginFailure) {
        recorded.status = options.loginFailure.status
        await json(route, options.loginFailure.status, options.loginFailure.body)
        return
      }

      let payload: { email?: string; password?: string } = {}
      try { payload = request.postDataJSON() as typeof payload } catch { /* Malformed synthetic request behaves as invalid credentials. */ }
      const authenticated = accounts.find((candidate) => candidate.email === payload.email && candidate.password === payload.password)
      if (!authenticated) {
        recorded.status = 401
        await json(route, 401, { detail: 'Invalid email or password.' })
        return
      }

      if (sessionToken) {
        const previous = sessions.get(sessionToken)
        if (previous) previous.revoked = true
      }
      sessionNumber += 1
      const nextToken = `synthetic-session-${authenticated.id}-${sessionNumber}`
      const csrfToken = `synthetic-csrf-${sessionNumber}`
      sessions.set(nextToken, { user: authenticated, revoked: false })
      await context.addCookies([
        { url: E2E_ORIGIN, name: 'skillfair_session', value: nextToken, httpOnly: true, sameSite: 'Lax' },
        { url: E2E_ORIGIN, name: 'skillfair_csrf', value: csrfToken, httpOnly: false, sameSite: 'Lax' },
      ])
      recorded.status = 200
      const publicUser = { id: authenticated.id, email: authenticated.email, created_at: authenticated.created_at }
      await json(route, 200, publicUser)
      return
    }

    if (path === '/api/auth/logout' && method === 'POST') {
      const csrfHeader = headers['x-csrf-token']
      recorded.csrfHeaderMatchesCookie = Boolean(csrfCookie && csrfHeader === csrfCookie)
      if (!user || !csrfCookie || csrfHeader !== csrfCookie || headers.origin !== E2E_ORIGIN) {
        recorded.status = 403
        await json(route, 403, { detail: 'CSRF validation failed.' })
      } else {
        if (session) session.revoked = true
        recorded.status = 204
        await context.clearCookies()
        await route.fulfill({ status: 204 })
      }
      return
    }

    if (path === '/api/skills/analyses/latest' && method === 'GET') {
      if (!user) {
        recorded.status = 401
        await json(route, 401, { detail: 'Authentication required.' })
        return
      }
      const analysis = options.analysisByUserId?.[user.id]
      recorded.status = analysis ? 200 : 404
      if (analysis) await json(route, 200, analysis)
      else await json(route, 404, { detail: 'No saved analysis was found.' })
      return
    }

    if (path === options.protectedUnauthorizedPath && user) {
      if (session) session.revoked = true
      recorded.status = 401
      await json(route, 401, { detail: 'Authentication required.' })
      return
    }

    // Every unconfigured API route is local and deterministic; no request can reach a backend.
    recorded.status = 404
    await json(route, 404, { detail: 'No synthetic response is configured for this frontend-only test.' })
  }

  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== E2E_ORIGIN) {
      await route.abort()
      return
    }
    if (url.pathname.startsWith('/api/')) {
      await handler(route)
      return
    }
    await route.continue()
  })

  return {
    requests,
    pauseNextRequest,
    dispose: () => context.unrouteAll({ behavior: 'wait' }),
  }
}

export async function openSignedOutPage(page: Page): Promise<void> {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expectSignedOut(page)
}

export async function expectSignedOut(page: Page): Promise<void> {
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Sign out/ })).toHaveCount(0)
}
