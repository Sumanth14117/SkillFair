import { expect, test, type Page } from '@playwright/test'
import { accountA, installAuthApiMock, openSignedOutPage } from './helpers/auth-api'
import { installProductApiMock } from './helpers/product-api'

async function openMarketPage(page: Page) {
  await openSignedOutPage(page)
  await page.getByRole('textbox', { name: 'Email address' }).fill(accountA.email)
  await page.getByRole('textbox', { name: 'Password' }).fill(accountA.password)
  await page.getByRole('button', { name: 'Sign in to SkillFair' }).click()
  await expect(page.getByRole('button', { name: 'Sign out Account A' })).toBeVisible()
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Market Intelligence', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Market Intelligence' })).toBeVisible()
}

const SOURCE = {
  source_id: 'adzuna-current-job-ads', source_name: 'Adzuna API', source_type: 'job-ad search index',
  verification_status: 'verified', retrieved_at: '2026-11-20T10:00:00Z', published_at: null,
  geography: 'India', source_reference: 'https://developer.adzuna.com/overview',
}

function insight(id: string, category: string, title: string, value: number) {
  return {
    insight_id: id, category, title,
    summary: 'Synthetic browser-test count. It is not a census, skill requirement, or candidate proficiency measure.',
    value, unit: 'matching job ads', period: 'Snapshot 2026-11-20 10:00 UTC', geography: 'Bengaluru',
    source_id: SOURCE.source_id,
    calculation_method: 'Synthetic route mock representing the documented provider count field; no extrapolation or deduplication.',
    data_quality: 'limited',
  }
}

test('Market Intelligence validates role input and honestly shows unavailable when no provider is configured', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  try {
    await openMarketPage(page)
    await expect(page.getByText(/No approved market-data provider is configured/)).toBeVisible()
    await page.getByRole('button', { name: 'Check availability' }).click()
    await expect(page.getByRole('alert')).toContainText('Enter a job title')
    expect(product.requests.some((request) => request.path === '/api/market/intelligence')).toBe(false)

    await page.getByLabel('Job title').fill('Backend Developer')
    await page.getByLabel('Geography').fill('Bengaluru')
    await page.getByRole('button', { name: 'Check availability' }).click()
    await expect(page.getByRole('heading', { name: 'Market insights aren’t available yet' })).toBeVisible()
    await expect(page.getByText('No market statistics are available.')).toBeVisible()
    await expect(page.getByText('Emerging or declining skills:')).toBeVisible()
    const request = product.requests.find((item) => item.path === '/api/market/intelligence')
    expect(request?.payload).toMatchObject({ job_title: 'Backend Developer', country_code: 'in', geography: 'Bengaluru' })
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Market Intelligence renders attributed role and entered-keyword counts with calculation limits', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  let submittedPayload: unknown
  let submittedCount = 0
  await page.route('**/api/market/intelligence', async (route) => {
    submittedCount += 1
    submittedPayload = route.request().postDataJSON()
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      job_title: 'Backend Developer', geography: 'Bengaluru', data_availability: 'available', freshness: 'current',
      sources: [SOURCE],
      insights: [
        insight('role-demand-current-ad-count', 'role_demand', 'Current job ads matching Backend Developer', 240),
        insight('role-skill-keyword-1', 'skill_demand', 'Job ads matching role and “Python”', 81),
        insight('role-skill-keyword-2', 'skill_demand', 'Job ads matching role and “SQL”', 64),
      ],
      message: 'Synthetic browser-test results are displayed only to exercise the sourced-data interface.',
    }) })
  })
  try {
    await openMarketPage(page)
    await page.getByLabel('Job title').fill('Backend Developer')
    await page.getByLabel('Geography').fill('Bengaluru')
    await page.getByLabel('Market country').selectOption('in')
    await page.getByLabel('Skill keywords').fill('Python\nSQL')
    await page.getByRole('button', { name: 'Check availability' }).click()
    await expect(page.getByRole('heading', { name: 'Backend Developer', exact: true })).toBeVisible()
    await expect(page.getByText('Verified data available')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Current job ads matching Backend Developer', exact: true })).toBeVisible()
    await expect(page.getByRole('progressbar', { name: /Current job ads matching Backend Developer: 240/ })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Job ads matching role and “Python”', exact: true })).toBeVisible()
    await expect(page.getByRole('progressbar', { name: /Job ads matching role and “Python”: 81/ })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Adzuna API', exact: true })).toBeVisible()
    await expect(page.getByText('Snapshot 2026-11-20 10:00 UTC').first()).toBeVisible()
    await expect(page.locator('.market-demand-chart')).toContainText('not a census, share of all jobs, or candidate skill evidence')
    expect(submittedPayload).toMatchObject({ job_title: 'Backend Developer', country_code: 'in', geography: 'Bengaluru', skill_queries: ['Python', 'SQL'] })
    expect(submittedCount).toBe(1)
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})

test('Market Intelligence displays provider failure without automatic retries', async ({ page }) => {
  const auth = await installAuthApiMock(page.context())
  const product = await installProductApiMock(page.context())
  let submittedCount = 0
  await page.route('**/api/market/intelligence', async (route) => {
    submittedCount += 1
    await route.fulfill({ status: 503, contentType: 'application/json', headers: { 'Retry-After': '60' }, body: JSON.stringify({ detail: 'The configured market-data source is temporarily rate limited. Try again later.' }) })
  })
  try {
    await openMarketPage(page)
    await page.getByLabel('Job title').fill('Backend Developer')
    await page.getByRole('button', { name: 'Check availability' }).click()
    await expect(page.getByRole('heading', { name: 'Market data could not be checked' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('temporarily rate limited')
    expect(submittedCount).toBe(1)
  } finally {
    await product.dispose()
    await auth.dispose()
  }
})
