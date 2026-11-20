import { expect, test } from '@playwright/test'
import {
  E2E_ORIGIN,
  accountA,
  accountB,
  expectSignedOut,
  installAuthApiMock,
  openSignedOutPage,
} from './helpers/auth-api'

const REGISTRATION_CONFIRMATION = 'Registration request processed. If the address already has an account, sign in. If it is new, sign in with the password you chose. SkillFair does not send verification email.'

async function signIn(page: import('@playwright/test').Page, email: string, password: string, displayName: string) {
  await page.getByRole('textbox', { name: 'Email address' }).fill(email)
  await page.getByRole('textbox', { name: 'Password' }).fill(password)
  await page.getByRole('button', { name: 'Sign in to SkillFair' }).click()
  await expect(page.getByRole('button', { name: `Sign out ${displayName}` })).toBeVisible()
}

test('registration shows the same generic confirmation for new and existing addresses', async ({ page }) => {
  const mock = await installAuthApiMock(page.context())
  try {
    await openSignedOutPage(page)
    await page.getByRole('tab', { name: 'Create account' }).click()
    await page.getByRole('textbox', { name: 'Email address' }).fill(accountA.email)
    await page.getByRole('textbox', { name: 'Password' }).fill(accountA.password)
    await page.getByRole('button', { name: 'Create secure account' }).click()
    await expect(page.getByRole('status')).toHaveText(REGISTRATION_CONFIRMATION)

    const first = mock.requests.find((request) => request.path === '/api/auth/register')
    expect(first?.status).toBe(202)
    expect(first?.origin).toBe(E2E_ORIGIN)
    expect(first?.hasSessionCookie).toBe(false)

    await page.getByRole('tab', { name: 'Create account' }).click()
    await page.getByRole('textbox', { name: 'Email address' }).fill(accountA.email)
    await page.getByRole('textbox', { name: 'Password' }).fill(accountA.password)
    await page.getByRole('button', { name: 'Create secure account' }).click()
    await expect(page.getByRole('status')).toHaveText(REGISTRATION_CONFIRMATION)

    const registrations = mock.requests.filter((request) => request.path === '/api/auth/register')
    expect(registrations).toHaveLength(2)
    expect(registrations.map(({ status, origin, hasSessionCookie }) => ({ status, origin, hasSessionCookie }))).toEqual([
      { status: 202, origin: E2E_ORIGIN, hasSessionCookie: false },
      { status: 202, origin: E2E_ORIGIN, hasSessionCookie: false },
    ])
    expect(await page.context().cookies(E2E_ORIGIN)).toEqual([])
  } finally {
    await mock.dispose()
  }
})

test('registration validates malformed form input before sending credentials', async ({ page }) => {
  const mock = await installAuthApiMock(page.context())
  try {
    await openSignedOutPage(page)
    await page.getByRole('tab', { name: 'Create account' }).click()
    await page.getByRole('textbox', { name: 'Email address' }).fill('not-an-email')
    await page.getByRole('textbox', { name: 'Password' }).fill(accountA.password)
    await page.getByRole('button', { name: 'Create secure account' }).click()
    await expect(page.getByRole('alert')).toHaveText('Enter a valid email address.')
    expect(mock.requests.some((request) => request.path === '/api/auth/register')).toBe(false)
  } finally {
    await mock.dispose()
  }
})

test('registration displays the backend safe validation response', async ({ page }) => {
  const mock = await installAuthApiMock(page.context(), {
    registrationFailure: { status: 422, body: { detail: 'Request validation failed.', errors: [{ location: ['body', 'password'], type: 'string_too_short', message: 'This value is too short.' }] } },
  })
  try {
    await openSignedOutPage(page)
    await page.getByRole('tab', { name: 'Create account' }).click()
    await page.getByRole('textbox', { name: 'Email address' }).fill(accountA.email)
    await page.getByRole('textbox', { name: 'Password' }).fill(accountA.password)
    await page.getByRole('button', { name: 'Create secure account' }).click()
    await expect(page.getByRole('alert')).toHaveText('Request validation failed.')
    expect(mock.requests.filter((request) => request.path === '/api/auth/register').map((request) => request.status)).toEqual([422])
  } finally {
    await mock.dispose()
  }
})

test('login displays pending state, restores the synthetic account, and receives HTTP-only session plus readable CSRF cookies', async ({ page }) => {
  const mock = await installAuthApiMock(page.context())
  const loginGate = mock.pauseNextRequest('/api/auth/login')
  try {
    await openSignedOutPage(page)
    await page.getByRole('textbox', { name: 'Email address' }).fill(accountA.email)
    await page.getByRole('textbox', { name: 'Password' }).fill(accountA.password)
    const submission = page.getByRole('button', { name: 'Sign in to SkillFair' }).click()
    await loginGate.started
    const pendingButton = page.getByRole('button', { name: 'Signing in…' })
    await expect(pendingButton).toBeDisabled()
    await expect(pendingButton).toBeVisible()
    loginGate.release()
    await submission

    await expect(page.getByRole('button', { name: 'Sign out Account A' })).toBeVisible()
    const cookies = await page.context().cookies(E2E_ORIGIN)
    expect(cookies.find((cookie) => cookie.name === 'skillfair_session')).toMatchObject({ httpOnly: true, sameSite: 'Lax' })
    expect(cookies.find((cookie) => cookie.name === 'skillfair_csrf')).toMatchObject({ httpOnly: false, sameSite: 'Lax' })
    expect(mock.requests.find((request) => request.path === '/api/auth/login')?.origin).toBe(E2E_ORIGIN)
  } finally {
    loginGate.release()
    await mock.dispose()
  }
})

test('incorrect credentials show the generic authentication failure', async ({ page }) => {
  const mock = await installAuthApiMock(page.context())
  try {
    await openSignedOutPage(page)
    await page.getByRole('textbox', { name: 'Email address' }).fill(accountA.email)
    await page.getByRole('textbox', { name: 'Password' }).fill('incorrect-synthetic-password')
    await page.getByRole('button', { name: 'Sign in to SkillFair' }).click()
    await expect(page.getByRole('alert')).toHaveText('Invalid email or password.')
    await expectSignedOut(page)
    expect(mock.requests.find((request) => request.path === '/api/auth/login')?.status).toBe(401)
  } finally {
    await mock.dispose()
  }
})

test('login service failure is shown safely and leaves the signed-out screen available', async ({ page }) => {
  const mock = await installAuthApiMock(page.context(), { loginFailure: { status: 503, body: { detail: 'private provider detail must not be shown' } } })
  try {
    await openSignedOutPage(page)
    await page.getByRole('textbox', { name: 'Email address' }).fill(accountA.email)
    await page.getByRole('textbox', { name: 'Password' }).fill(accountA.password)
    await page.getByRole('button', { name: 'Sign in to SkillFair' }).click()
    await expect(page.getByRole('alert')).toHaveText('Authentication is temporarily unavailable. Please try again.')
    await expectSignedOut(page)
  } finally {
    await mock.dispose()
  }
})

test('a missing session cannot open the authenticated workspace, even with a saved page locator', async ({ page }) => {
  const mock = await installAuthApiMock(page.context())
  await page.addInitScript(({ ownerId }) => {
    sessionStorage.setItem('skillfair.session.v2', JSON.stringify({
      version: 2,
      ownerId,
      analysisId: null,
      skillCount: 0,
      analysisMode: null,
      page: 'Settings',
      runId: null,
      careerDraft: { jobTitle: '', jobDescription: '', geography: '', essentialSkills: '', preferredSkills: '' },
    }))
  }, { ownerId: accountA.id })
  try {
    await page.goto('/?page=Settings', { waitUntil: 'domcontentloaded' })
    await expectSignedOut(page)
    await expect(page.getByRole('heading', { name: 'Settings' })).toHaveCount(0)
    expect(mock.requests.find((request) => request.path === '/api/auth/me')?.status).toBe(401)
  } finally {
    await mock.dispose()
  }
})

test('session restoration survives refresh and returns to the authenticated page', async ({ page }) => {
  const mock = await installAuthApiMock(page.context())
  try {
    await openSignedOutPage(page)
    await signIn(page, accountA.email, accountA.password, 'Account A')
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()

    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('button', { name: 'Sign out Account A' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
    expect(mock.requests.filter((request) => request.path === '/api/auth/me' && request.status === 200).length).toBeGreaterThanOrEqual(1)
  } finally {
    await mock.dispose()
  }
})

test('logout sends the matching CSRF token and returns the protected application to sign-in', async ({ page }) => {
  const mock = await installAuthApiMock(page.context())
  try {
    await openSignedOutPage(page)
    await signIn(page, accountA.email, accountA.password, 'Account A')
    await page.getByRole('button', { name: 'Sign out Account A' }).click()
    await expectSignedOut(page)
    const logout = mock.requests.find((request) => request.path === '/api/auth/logout')
    expect(logout).toMatchObject({ status: 204, csrfHeaderMatchesCookie: true, origin: E2E_ORIGIN, userId: accountA.id })
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0)

    await page.reload({ waitUntil: 'domcontentloaded' })
    await expectSignedOut(page)
  } finally {
    await mock.dispose()
  }
})

test('an expired session on a protected API response clears the authenticated workspace', async ({ page }) => {
  const analysis = {
    analysis_id: 'analysis_account_a_synthetic_0001',
    analysis_mode: 'local',
    skills: [{
      skill_name: 'Python', evidence_type: 'explicit', detection_method: 'local',
      supporting_excerpt: 'Synthetic resume evidence.', relevance_explanation: 'Synthetic test data.', validation_status: 'evidence_found',
    }],
  }
  const mock = await installAuthApiMock(page.context(), {
    analysisByUserId: { [accountA.id]: analysis },
    protectedUnauthorizedPath: '/api/jobs/match',
  })
  try {
    await openSignedOutPage(page)
    await page.evaluate(({ ownerId }) => {
    sessionStorage.setItem('skillfair.session.v2', JSON.stringify({
      version: 2, ownerId, analysisId: null, skillCount: 0, analysisMode: null,
      page: 'Job Matching', runId: null,
      careerDraft: { jobTitle: '', jobDescription: '', geography: '', essentialSkills: '', preferredSkills: '' },
    }))
    }, { ownerId: accountA.id })
    await signIn(page, accountA.email, accountA.password, 'Account A')
    await expect(page.getByRole('heading', { name: 'Job Matching' })).toBeVisible()
    await page.getByRole('textbox', { name: 'Job title' }).fill('Synthetic Engineer')
    await page.getByRole('textbox', { name: 'Job description' }).fill('Synthetic role description.')
    await page.getByRole('textbox', { name: /Required skills/ }).fill('Python')
    await page.getByRole('button', { name: 'Compare skills' }).click()
    await expectSignedOut(page)
    await expect(page.getByRole('alert')).toHaveText('Your sign-in session has expired. Sign in again.')
    expect(mock.requests.some((request) => request.path === '/api/jobs/match' && request.method === 'POST' && request.status === 401 && request.userId === accountA.id)).toBe(true)
  } finally {
    await mock.dispose()
  }
})

test('cross-tab logout and account switch revalidate identity and clear Account A draft before showing Account B', async ({ browser }) => {
  const context = await browser.newContext()
  const mock = await installAuthApiMock(context)
  const firstTab = await context.newPage()
  const secondTab = await context.newPage()
  try {
    await Promise.all([openSignedOutPage(firstTab), openSignedOutPage(secondTab)])
    await signIn(firstTab, accountA.email, accountA.password, 'Account A')
    await expect(secondTab.getByRole('button', { name: 'Sign out Account A' })).toBeVisible()

    await firstTab.getByRole('button', { name: 'Career Orchestrator', exact: true }).click()
    const title = firstTab.getByRole('textbox', { name: 'Job title' })
    await title.fill('Account A confidential draft role')
    await expect(title).toHaveValue('Account A confidential draft role')
    expect(await firstTab.evaluate(() => sessionStorage.getItem('skillfair.session.v2'))).toContain('Account A confidential draft role')

    await secondTab.getByRole('button', { name: 'Sign out Account A' }).click()
    await expectSignedOut(firstTab)
    expect(await firstTab.evaluate(() => sessionStorage.getItem('skillfair.session.v2'))).toBeNull()

    await signIn(secondTab, accountB.email, accountB.password, 'Account B')
    await expect(firstTab.getByRole('button', { name: 'Sign out Account B' })).toBeVisible()
    await expect(firstTab.getByText('Account A confidential draft role')).toHaveCount(0)
    await firstTab.getByRole('button', { name: 'Career Orchestrator', exact: true }).click()
    await expect(firstTab.getByRole('textbox', { name: 'Job title' })).toHaveValue('')
    expect(mock.requests.some((request) => request.path === '/api/auth/me' && request.userId === accountB.id)).toBe(true)
  } finally {
    await mock.dispose()
    await context.close()
  }
})

test('a delayed Account A analysis response cannot repopulate Account B after cross-tab switching', async ({ browser }) => {
  const analysisA = {
    analysis_id: 'analysis_account_a_synthetic_0001',
    analysis_mode: 'local',
    skills: [{
      skill_name: 'Account A confidential skill',
      evidence_type: 'explicit',
      detection_method: 'local',
      supporting_excerpt: 'Synthetic resume evidence line.',
      relevance_explanation: 'Synthetic test evidence only.',
      validation_status: 'evidence_found',
    }],
  }
  const context = await browser.newContext()
  const mock = await installAuthApiMock(context, { analysisByUserId: { [accountA.id]: analysisA } })
  const delayed = mock.pauseNextRequest('/api/skills/analyses/latest', accountA.id)
  const firstTab = await context.newPage()
  const secondTab = await context.newPage()
  try {
    await Promise.all([openSignedOutPage(firstTab), openSignedOutPage(secondTab)])
    await signIn(firstTab, accountA.email, accountA.password, 'Account A')
    await delayed.started
    await expect(secondTab.getByRole('button', { name: 'Sign out Account A' })).toBeVisible()

    await secondTab.getByRole('button', { name: 'Sign out Account A' }).click()
    await expectSignedOut(firstTab)
    await signIn(secondTab, accountB.email, accountB.password, 'Account B')
    await expect(firstTab.getByRole('button', { name: 'Sign out Account B' })).toBeVisible()

    delayed.release()
    await firstTab.getByRole('button', { name: 'Skills Discovery', exact: true }).click()
    await expect(firstTab.getByRole('heading', { name: 'Upload a candidate resume' })).toBeVisible()
    await expect(firstTab.getByRole('heading', { name: 'Account A confidential skill' })).toHaveCount(0)
    expect(await firstTab.evaluate(() => sessionStorage.getItem('skillfair.session.v2'))).not.toContain('analysis_account_a_synthetic_0001')
  } finally {
    delayed.release()
    await mock.dispose()
    await context.close()
  }
})
