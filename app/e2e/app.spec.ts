/** The assertion suite: the app against a REAL running server on a demo
 * vault (e2e/serve.sh plants one uncategorized transaction first).
 * Money stays server-formatted; these tests assert flow, not arithmetic —
 * the API contract tests own the to-the-dollar checks. */
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const SHOTS = process.env.SARA_SHOTS ?? 'e2e/shots'
const ROOMS: [string, string, string][] = [
  ['spending', 'Spending', '.phero'],
  ['activity', 'Activity', '.feed'],
  ['map', 'Money map', '.chart-nw, .chart-map'],
  ['investments', 'Investments', '.postable, .empty'],
  ['goals', 'Goals', '.setrow'],
  ['autopilot', 'Autopilot', '.lanes, .allclear, .needs'],
]

async function settled(page: Page): Promise<void> {
  await page.waitForFunction(() => !document.querySelector('.room .skeleton'), undefined, {
    timeout: 30_000,
  })
}

test.describe.configure({ mode: 'serial' })

test('the glance loads with verdicts and the Next line', async ({ page }) => {
  await page.goto('/')
  await page.waitForSelector('.tiles .tv-big', { timeout: 30_000 })
  await expect(page.locator('.hi')).toContainText(/Good (morning|afternoon|evening)/)
  await expect(page.locator('.say')).not.toBeEmpty()
  await expect(page.locator('.tile')).toHaveCount(4)
  await expect(page.locator('.next .nk')).toBeVisible()
  await settled(page)
  await page.screenshot({ path: `${SHOTS}/e2e-glance.png` })
})

test('every room opens and renders its content', async ({ page }) => {
  await page.goto('/')
  await page.waitForSelector('.tiles .tv-big', { timeout: 30_000 })
  for (const [id, label, marker] of ROOMS) {
    await page.click(`.tab:has-text("${label}")`)
    await settled(page)
    await expect(page.locator(marker).first()).toBeVisible()
    await expect(page.locator('.loaderr')).toHaveCount(0)
    await page.screenshot({ path: `${SHOTS}/e2e-room-${id}.png`, fullPage: true })
  }
  // two rooms in dark for the theme pass
  await page.click('.themebtn') // auto -> light
  await page.click('.themebtn') // light -> dark
  await page.click('.tab:has-text("Spending")')
  await settled(page)
  await page.screenshot({ path: `${SHOTS}/e2e-dark-spending.png`, fullPage: true })
  await page.click('.tab:has-text("Money map")')
  await settled(page)
  await page.screenshot({ path: `${SHOTS}/e2e-dark-map.png`, fullPage: true })
})

test('teaching a rule categorizes the planted transaction', async ({ page }) => {
  await page.goto('/#activity')
  await page.waitForSelector('.feed', { timeout: 30_000 })
  const chip = page.locator('.catchip.uncat').first()
  await expect(chip).toBeVisible()
  await chip.click()
  const teach = page.locator('.teach')
  await expect(teach).toBeVisible()
  await expect(teach.locator('input[type=text]')).toHaveValue(/PLANTED COFFEE/)
  await teach.locator('select').selectOption({ index: 1 })
  await teach.locator('.btn.primary').click()
  await expect(page.locator('.toast')).toContainText('Taught Sara a rule', { timeout: 30_000 })
  // the feed refetches through the server: the planted payee now wears a
  // real category chip — recategorize actually rewrote the ledger
  await page.waitForFunction(() => !document.querySelector('.room .skeleton'), undefined, {
    timeout: 30_000,
  })
  const planted = page.locator('.feed li', { hasText: 'PLANTED COFFEE' }).first()
  await expect(planted.locator('.catchip:not(.uncat)')).toBeVisible({ timeout: 15_000 })
  await page.screenshot({ path: `${SHOTS}/e2e-taught.png` })
})

test('dismissing a finding quiets it, undo restores it', async ({ page }) => {
  await page.goto('/#autopilot')
  await page.waitForSelector('.room .card', { timeout: 30_000 })
  await settled(page)
  const rows = page.locator('.needs li')
  if ((await rows.count()) === 0) {
    test.skip(true, 'vault has no open findings')
  }
  const first = rows.first()
  const title = await first.locator('.why').textContent()
  await first.hover()
  await first.locator('.dismissbtn').click()
  await expect(page.locator('.toast')).toContainText('Quiet until', { timeout: 15_000 })
  await settled(page)
  if (title) {
    await expect(page.locator('.needs li', { hasText: title.slice(0, 30) })).toHaveCount(0)
  }
  await page.locator('.toast button', { hasText: 'Undo' }).click()
  await settled(page)
  await expect(page.locator('.needs li').first()).toBeVisible()
})

test('phone viewport holds together', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.waitForSelector('.tiles .tv-big', { timeout: 30_000 })
  await settled(page)
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(2)
  await page.screenshot({ path: `${SHOTS}/e2e-phone.png`, fullPage: true })
})
