import { expect, test } from '@playwright/test'

const localOrigin = 'http://127.0.0.1:4174'

test('frontend smoke: renders the signed-out SkillFair interface without a backend', async ({ page }) => {
  const apiRequests: string[] = []
  page.on('request', (request) => {
    const requestUrl = request.url()
    if (requestUrl.startsWith(`${localOrigin}/api/`)) {
      const path = requestUrl.slice(localOrigin.length).split('?', 1)[0]
      apiRequests.push(`${request.method()} ${path}`)
    }
  })

  await page.route('**/*', async (route) => {
    if (route.request().url().startsWith(localOrigin)) {
      await route.continue()
      return
    }
    await route.abort()
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  await expect(page).toHaveTitle(/SkillFair — Skills-first hiring/)
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  await expect(page.getByRole('tablist', { name: 'Account access' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Email address' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Password' })).toBeVisible()

  expect(apiRequests.length).toBeGreaterThan(0)
  expect(apiRequests.every((request) => request === 'GET /api/auth/me')).toBe(true)
})
