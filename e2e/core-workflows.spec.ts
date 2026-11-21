import { expect, test, type Page } from '@playwright/test'
import {
  accountA, accountB, installAuthApiMock, openSignedOutPage,
} from './helpers/auth-api'
import { installProductApiMock, RUN_A } from './helpers/product-api'

// Node exposes Buffer at runtime; the local declaration avoids adding @types/node
// solely for browser-test fixture bytes.
declare const Buffer: { from(contents: string): Uint8Array }
const syntheticFile = (contents: string) => Buffer.from(contents)

async function signIn(page: Page, account = accountA) {
  await openSignedOutPage(page)
  await page.getByRole('textbox', { name: 'Email address' }).fill(account.email)
  await page.getByRole('textbox', { name: 'Password' }).fill(account.password)
  await page.getByRole('button', { name: 'Sign in to SkillFair' }).click()
  await expect(page.getByRole('button', { name: /^Sign out Account [AB]$/ })).toBeVisible()
}

async function openPage(page: Page, name: string) {
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name, exact: true }).click()
}

async function createAnalysis(page: Page) {
  await openPage(page, 'Skills Discovery')
  await page.locator('input[type="file"]').setInputFiles({
    name: 'synthetic-resume.pdf', mimeType: 'application/pdf', buffer: syntheticFile('%PDF-1.4 synthetic browser fixture'),
  })
  await expect(page.getByText('synthetic-resume.pdf')).toBeVisible()
  await page.getByRole('button', { name: 'Discover skills locally' }).click()
  await expect(page.getByText('Local deterministic analysis complete')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Python', exact: true })).toBeVisible()
}

async function createWorkflow(page: Page, title = 'Data Engineer') {
  await page.getByLabel('Job title', { exact: true }).fill(title)
  await page.getByLabel('Job description').fill('Build reliable data services and review requirements with a human recruiter.')
  await page.getByLabel('Essential skills').fill('Python')
  await page.getByRole('button', { name: 'Start career workflow' }).click()
  await expect(page.getByText('Synthetic workflow awaiting a human review decision.')).toBeVisible()
}

async function markReviewRequirement(page: Page) {
  await page.getByRole('checkbox', { name: /req-001/ }).check()
  await expect(page.getByText('1 of 1 requirements reviewed')).toBeVisible()
}

test('Skills Discovery validates file input, displays analysis, and restores the saved account analysis after refresh', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  try {
    await signIn(page)
    await openPage(page, 'Skills Discovery')
    await page.locator('input[type="file"]').setInputFiles({ name: 'unsupported.txt', mimeType: 'text/plain', buffer: syntheticFile('synthetic') })
    await expect(page.getByRole('alert')).toContainText('Choose a PDF or DOCX file.')
    expect(product.requests.some((request) => request.path === '/api/skills/discover')).toBe(false)

    await page.locator('input[type="file"]').setInputFiles({ name: 'synthetic-resume.pdf', mimeType: 'application/pdf', buffer: syntheticFile('%PDF-1.4 synthetic browser fixture') })
    await page.getByRole('button', { name: 'Discover skills locally' }).click()
    await expect(page.getByText('Local deterministic analysis complete')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Python', exact: true })).toBeVisible()
    await expect(page.getByText('View resume evidence')).toBeVisible()
    expect(product.requests.filter((request) => request.path === '/api/skills/discover')).toHaveLength(1)

    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Skills Discovery' })).toBeVisible()
    await openPage(page, 'Skills Discovery')
    await expect(page.getByText('Local deterministic analysis · restored')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Python', exact: true })).toBeVisible()
    expect(product.requests.filter((request) => request.path === '/api/skills/analyses/latest').length).toBeGreaterThan(1)
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Skills Discovery shows a safe service error without clearing the ability to retry', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  await page.route('**/api/skills/discover', async (route) => {
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic service outage.' }) })
  })
  try {
    await signIn(page)
    await openPage(page, 'Skills Discovery')
    await page.locator('input[type="file"]').setInputFiles({ name: 'synthetic-resume.pdf', mimeType: 'application/pdf', buffer: syntheticFile('%PDF-1.4 synthetic browser fixture') })
    await page.getByRole('button', { name: 'Discover skills locally' }).click()
    await expect(page.getByRole('alert')).toContainText('Synthetic service outage.')
    await expect(page.getByRole('button', { name: 'Try again' })).toBeEnabled()
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Career Orchestrator creates a workflow, lists it, and restores the exact selected workflow ID', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  try {
    await signIn(page)
    await createAnalysis(page)
    await openPage(page, 'Career Orchestrator')
    await expect(page.getByRole('heading', { name: 'Recent Workflows' })).toBeVisible()
    await expect(page.getByText('No saved workflows yet')).toBeVisible()
    await createWorkflow(page, 'Data Engineer')
    const historyCard = page.getByRole('article').filter({ hasText: 'Data Engineer' }).filter({ hasText: 'Continue Workflow' })
    await expect(historyCard).toBeVisible()
    await expect(historyCard).toContainText('Pending human review')
    await historyCard.getByRole('button', { name: 'Continue Workflow' }).click()
    await expect(page.locator('#career-summary').getByText(`Workflow ID · ${RUN_A}`)).toBeVisible()
    expect(product.requests.some((request) => request.method === 'GET' && request.path === `/api/career/orchestrator/runs/${RUN_A}`)).toBe(true)
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Career Orchestrator does not fall back to another history item when the selected workflow is unavailable', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  try {
    await signIn(page)
    await createAnalysis(page)
    await openPage(page, 'Career Orchestrator')
    await createWorkflow(page, 'First Synthetic Role')
    await page.getByRole('button', { name: 'Start a new workflow' }).click()
    await createWorkflow(page, 'Second Synthetic Role')
    const firstCard = page.locator('.career-history-item').filter({ hasText: 'First Synthetic Role' })
    const requestedRunIds: string[] = []
    await page.route(`**/api/career/orchestrator/runs/${RUN_A}`, async (route) => {
      if (route.request().method() === 'GET') {
        requestedRunIds.push(new URL(route.request().url()).pathname.split('/').at(-1) ?? '')
        await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not available' }) })
      }
      else await route.fallback()
    })
    await firstCard.getByRole('button', { name: 'Continue Workflow' }).click()
    await expect(firstCard.locator('.career-history-item-error')).toContainText('It was not replaced with another workflow.')
    expect(requestedRunIds).toEqual([RUN_A])
    expect(product.requests.some((request) => request.method === 'GET' && request.path.startsWith('/api/career/orchestrator/runs/'))).toBe(false)
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

for (const decision of ['proceed', 'revise', 'stop'] as const) {
  test(`Career Orchestrator records ${decision} and shows the matching visible state`, async ({ page }) => {
    const auth = await installAuthApiMock(page.context())
    const product = await installProductApiMock(page.context())
    try {
      await signIn(page)
      await createAnalysis(page)
      await openPage(page, 'Career Orchestrator')
      await createWorkflow(page)
      await markReviewRequirement(page)
      const buttonName = decision === 'proceed' ? 'Proceed to Learning Pathways' : decision[0].toUpperCase() + decision.slice(1)
      await page.getByRole('button', { name: buttonName }).click()
      await expect(page.locator('.career-review-recorded')).toContainText(`Decision recorded: ${decision}`)
      expect(product.requests.some((request) => request.method === 'POST' && request.path.endsWith('/review') && (request.payload as { decision?: string })?.decision === decision)).toBe(true)
      if (decision === 'proceed') {
        await expect(page.getByText('Build a small project')).toBeVisible()
      } else if (decision === 'revise') {
        await expect(page.locator('.career-learning-locked').getByText('Learning activities are held for human review', { exact: true })).toBeVisible()
      } else {
        await expect(page.getByText('Workflow stopped')).toBeVisible()
      }
    } finally {
      await product.dispose()
      await auth.dispose()
    }
  })
}

test('Career Orchestrator blocks duplicate review submission while the first request is pending', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  const gate = product.pauseNext(`/api/career/orchestrator/runs/${RUN_A}/review`, accountA.id)
  try {
    await signIn(page)
    await createAnalysis(page)
    await openPage(page, 'Career Orchestrator')
    await createWorkflow(page)
    await markReviewRequirement(page)
    await page.getByRole('button', { name: 'Proceed to Learning Pathways' }).click()
    await gate.started
    await expect(page.getByRole('button', { name: 'Recording…' })).toBeDisabled()
    expect(product.requests.filter((request) => request.method === 'POST' && request.path.endsWith('/review'))).toHaveLength(1)
    gate.release()
    await expect(page.locator('.career-review-recorded')).toContainText('Decision recorded: proceed')
  } finally {
    gate.release()
    await product.dispose()
    await auth.dispose()
  }
})

test('Bias Audit displays findings, saves review selections, and restores them from account history', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  try {
    await signIn(page)
    await openPage(page, 'Bias Audit')
    await expect(page.getByText('No saved audits yet.')).toBeVisible()
    await page.getByLabel(/^Job title/).fill('Data Engineer')
    await page.getByLabel('Job description').fill('Minimum 8 years of continuous experience.')
    await page.getByRole('button', { name: 'Run Bias Audit' }).click()
    await expect(page.getByText('BA-EXPERIENCE-CONTINUITY-001')).toBeVisible()
    await expect(page.getByText('Unsaved draft')).toBeVisible()
    await page.getByRole('button', { name: 'Save audit' }).click()
    await expect(page.getByText('Saved to this account')).toBeVisible()
    const reviewGroup = page.getByRole('group', { name: 'Review status for BA-EXPERIENCE-CONTINUITY-001' })
    await reviewGroup.getByRole('button', { name: 'Accepted' }).click()
    await expect(reviewGroup.getByRole('button', { name: 'Accepted' })).toHaveAttribute('aria-pressed', 'true')

    await openPage(page, 'Career Orchestrator')
    await openPage(page, 'Bias Audit')
    await expect(page.getByText('BA-EXPERIENCE-CONTINUITY-001')).toBeVisible()
    const restoredGroup = page.getByRole('group', { name: 'Review status for BA-EXPERIENCE-CONTINUITY-001' })
    await expect(restoredGroup.getByRole('button', { name: 'Accepted' })).toHaveAttribute('aria-pressed', 'true')
    expect(product.requests.some((request) => request.method === 'GET' && request.path.startsWith('/api/jobs/audits/'))).toBe(true)
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Bias Audit surfaces a malformed successful response instead of rendering fabricated results', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  await page.route('**/api/jobs/audit', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ findings: 'not-an-array' }) })
  })
  try {
    await signIn(page)
    await openPage(page, 'Bias Audit')
    await page.getByLabel('Job description').fill('A synthetic criteria description.')
    await page.getByRole('button', { name: 'Run Bias Audit' }).click()
    await expect(page.getByRole('heading', { name: 'Audit could not be completed' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('unsupported response')
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Impact Measurement renders sourced aggregate availability and accepts consented candidate feedback', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  try {
    await signIn(page)
    await openPage(page, 'Impact Measurement')
    await expect(page.getByRole('heading', { name: 'Average recruiter review time' })).toBeVisible()
    await expect(page.getByText('5.2 min')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Candidate application experience' })).toBeVisible()
    await expect(page.getByText('awaiting data').first()).toBeVisible()
    await expect(page.getByText('Candidate feedback is not anonymous')).toBeVisible()

    const feedbackForm = page.getByRole('form', { name: 'Candidate feedback' })
    const submit = feedbackForm.getByRole('button', { name: 'Submit candidate feedback' })
    await expect(submit).toBeDisabled()
    await feedbackForm.getByLabel('How was your overall application experience?').selectOption('4')
    await feedbackForm.getByLabel('How accessible was the process for you?').selectOption('4')
    await feedbackForm.getByLabel('How clear were the job requirements?').selectOption('5')
    await feedbackForm.getByLabel('How fair did the process feel to you?').selectOption('4')
    await feedbackForm.getByLabel(/I voluntarily agree that these ratings/).check()
    await expect(submit).toBeEnabled()
    await submit.click()
    await expect(feedbackForm.getByRole('status')).toContainText('Synthetic feedback was accepted')
    const submitted = product.requests.find((request) => request.path === '/api/impact/feedback/candidate')
    expect(submitted?.userId).toBe(accountA.id)
    expect((submitted?.payload as { consent_to_aggregate?: boolean })?.consent_to_aggregate).toBe(true)
    expect(submitted?.payload).not.toHaveProperty('comment')
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Impact Measurement distinguishes an empty awaiting-data account from measured zero', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  await page.route('**/api/impact/summary**', async (route) => {
    // Return a valid empty account summary while preserving the actual page and rendering path.
    const body = await (await import('./helpers/product-api')).buildEmptyImpactSummary()
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
  try {
    await signIn(page)
    await openPage(page, 'Impact Measurement')
    await expect(page.getByRole('heading', { name: 'Completed reviews' })).toBeVisible()
    await expect(page.getByText('awaiting data').first()).toBeVisible()
    await expect(page.locator('.impact-chart-empty').first()).toContainText('No fabricated chart values are shown.')
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Notifications become read and remain read while navigating within the authenticated shell', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  try {
    await signIn(page)
    await createAnalysis(page)
    await openPage(page, 'Career Orchestrator')
    await createWorkflow(page)
    await markReviewRequirement(page)
    await page.getByRole('button', { name: 'Proceed to Learning Pathways' }).click()
    await expect(page.locator('.career-review-recorded')).toContainText('Decision recorded: proceed')
    await page.getByRole('button', { name: 'Notifications' }).click()
    await expect(page.getByText('Review decision recorded')).toBeVisible()
    await expect(page.getByText('1 unread')).toBeVisible()
    await page.getByRole('button', { name: /Review decision recorded/ }).click()
    await openPage(page, 'Settings')
    await page.getByRole('button', { name: 'Notifications' }).click()
    await expect(page.getByText('0 unread')).toBeVisible()
    await expect(product.requests.some((request) => request.method === 'POST' && request.path.includes('/read'))).toBe(true)
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Account B never sees Account A workflows after switching while an Account A history response is pending', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  let gate: ReturnType<typeof product.pauseNext> | null = null
  try {
    await signIn(page, accountA)
    await createAnalysis(page)
    await openPage(page, 'Career Orchestrator')
    await createWorkflow(page, 'Account A Confidential Synthetic Role')
    gate = product.pauseNext('/api/career/orchestrator/runs', accountA.id)
    await page.getByRole('button', { name: 'Refresh workflow history' }).click()
    await gate.started

    await page.getByRole('button', { name: 'Sign out Account A' }).click()
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
    await page.getByRole('textbox', { name: 'Email address' }).fill(accountB.email)
    await page.getByRole('textbox', { name: 'Password' }).fill(accountB.password)
    await page.getByRole('button', { name: 'Sign in to SkillFair' }).click()
    await expect(page.getByRole('button', { name: 'Sign out Account B' })).toBeVisible()
    await openPage(page, 'Career Orchestrator')
    await expect(page.getByText('No saved workflows yet')).toBeVisible()
    gate.release()
    await expect(page.getByText('Account A Confidential Synthetic Role')).toHaveCount(0)
    expect(product.requests.some((request) => request.userId === accountA.id && request.path === '/api/career/orchestrator/runs')).toBe(true)
    expect(product.requests.some((request) => request.userId === accountB.id && request.path === '/api/career/orchestrator/runs')).toBe(true)
  } finally {
    gate?.release()
    await product.dispose()
    await auth.dispose()
  }
})
