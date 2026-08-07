/** Live-suggestion visual pass — run by hand against a REAL vault's server
 * (read-only): SARA_SUGGEST_LOOK=1 SARA_E2E_URL=http://127.0.0.1:<port>
 * SARA_SHOTS=<dir> npx playwright test suggest-look. Drives the real
 * on-device model: a business row must show "Sara thinks: … (on-device)"
 * preselected; a person/P2P row must show the line WITHOUT preselecting.
 * Skipped everywhere else (the mocked app.spec tests own CI behavior). */
import { expect, test } from '@playwright/test'

const SHOTS = process.env.SARA_SHOTS ?? 'e2e/shots'
const LIVE = process.env.SARA_SUGGEST_LOOK === '1'

test.describe(() => {
  test.skip(!LIVE, 'set SARA_SUGGEST_LOOK=1 (and SARA_E2E_URL) for the live pass')

  test('a real business row draws a live on-device suggestion', async ({ page }) => {
    test.setTimeout(180_000)
    const payee = process.env.SARA_LOOK_BUSINESS ?? 'sweetgreen'
    await page.goto('/#activity')
    await page.waitForSelector('.feed li', { timeout: 30_000 })
    await page.fill('.searchbox input', payee)
    const row = page.locator('.feed li:has(.catchip.uncat)').first()
    await expect(row).toBeVisible({ timeout: 15_000 })
    await row.locator('.catchip.uncat').click()
    const teach = page.locator('.teach')
    await expect(teach).toBeVisible() // instantly — the model answers async
    await expect(teach.locator('.saraline')).toContainText('on-device', {
      timeout: 60_000,
    })
    await expect(teach.locator('select')).not.toHaveValue('')
    await page.screenshot({ path: `${SHOTS}/real-suggest-business.png` })
  })

  test('a real person-payee row suggests but never preselects', async ({ page }) => {
    test.setTimeout(180_000)
    const payee = process.env.SARA_LOOK_PERSON ?? 'zelle payment to'
    await page.goto('/#activity')
    await page.waitForSelector('.feed li', { timeout: 30_000 })
    await page.fill('.searchbox input', payee)
    const row = page.locator('.feed li:has(.catchip.uncat)').first()
    await expect(row).toBeVisible({ timeout: 15_000 })
    await row.locator('.catchip.uncat').click()
    const teach = page.locator('.teach')
    await expect(teach).toBeVisible()
    await expect(teach.locator('.saraline')).toContainText('a person needs your word', {
      timeout: 60_000,
    })
    await expect(teach.locator('select')).toHaveValue('') // their word, not the model's
    await page.screenshot({ path: `${SHOTS}/real-suggest-person.png` })
  })
})
