/** The assertion suite: the app against a REAL running server on a demo
 * vault (e2e/serve.sh plants teach targets and wires the Plaid fixture
 * seam). Money stays server-formatted; these tests assert flow, not
 * arithmetic — the API contract tests own the to-the-dollar checks. */
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SHOTS = process.env.SARA_SHOTS ?? 'e2e/shots'
const HERE = path.dirname(fileURLToPath(import.meta.url))
const UPLOAD_FIXTURE = path.resolve(
  HERE, '../../skills/finance/sara/tests/fixtures/upload.checking4321.qfx')

const ROOMS: [string, string, string][] = [
  ['spending', 'Spending', '.phero'],
  ['activity', 'Activity', '.feed'],
  ['map', 'Money map', '.chart-nw, .chart-map'],
  ['investments', 'Investments', '.postable, .empty'],
  ['goals', 'Goals', '.setrow'],
  ['autopilot', 'Autopilot', '.lanes, .allclear, .needs'],
  ['connections', 'Connections', '.dropzone'],
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

test('activity search narrows the feed server-side', async ({ page }) => {
  await page.goto('/#activity')
  await page.waitForSelector('.feed', { timeout: 30_000 })
  await page.fill('.searchbox input', 'planted')
  await expect(page.locator('.feed li')).toHaveCount(3, { timeout: 15_000 })
  await expect(page.locator('.feedcount')).toContainText('3 rows')
  await page.fill('.searchbox input', '')
  await expect(page.locator('.feed li').first()).toBeVisible()
})

test('the owner lens re-slices activity and the map', async ({ page }) => {
  await page.goto('/')
  await page.waitForSelector('.tiles .tv-big', { timeout: 30_000 })
  const lens = page.locator('.lens')
  await expect(lens).toBeVisible()
  await lens.locator('button', { hasText: 'Alex' }).click()
  await expect(page.locator('.lensnote')).toContainText('Alex')
  await page.click('.tab:has-text("Activity")')
  await page.waitForSelector('.feed li', { timeout: 30_000 })
  await expect(page.locator('.feed li .ownerchip').first()).toHaveText('alex')
  await page.click('.tab:has-text("Money map")')
  await settled(page)
  await expect(page.locator('.chart-map')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/e2e-lens-map.png`, fullPage: true })
  await lens.locator('button', { hasText: 'All' }).click()
  await expect(page.locator('.lensnote')).toHaveCount(0)
})

test('teaching a rule categorizes the planted transaction', async ({ page }) => {
  await page.goto('/#activity')
  await page.waitForSelector('.feed', { timeout: 30_000 })
  const row = page.locator('.feed li', { hasText: 'PLANTED COFFEE' }).first()
  await row.locator('.catchip.uncat').click()
  const teach = page.locator('.teach')
  await expect(teach).toBeVisible()
  await expect(teach.locator('input[type=text]')).toHaveValue(/PLANTED COFFEE/)
  await teach.locator('select').selectOption({ index: 1 })
  await teach.locator('.btn.primary').click()
  await expect(page.locator('.toast')).toContainText('Taught Sara a rule', { timeout: 30_000 })
  // the optimistic override flips the chip at once; the DB converges behind
  await expect(row.locator('.catchip:not(.uncat)')).toBeVisible({ timeout: 15_000 })
  await page.screenshot({ path: `${SHOTS}/e2e-taught.png` })
})

test('bulk teach: two same-merchant rows become one rule', async ({ page }) => {
  await page.goto('/#activity')
  await page.waitForSelector('.feed', { timeout: 30_000 })
  await page.fill('.searchbox input', 'planted bagel')
  await expect(page.locator('.feed li')).toHaveCount(2, { timeout: 15_000 })
  for (const box of await page.locator('.feed li .fcheck').all()) {
    await box.check()
  }
  const bar = page.locator('.bulkbar')
  await expect(bar).toBeVisible()
  await expect(bar.locator('.bcount').first()).toContainText('2 selected')
  await bar.locator('select').selectOption({ index: 1 })
  await bar.locator('.btn.primary').click()
  await expect(page.locator('.toast')).toContainText('Taught Sara a rule for 2 rows', {
    timeout: 30_000,
  })
  await expect(page.locator('.catchip.uncat')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/e2e-bulk-taught.png` })
})

test('the palette jumps to an account register', async ({ page }) => {
  await page.goto('/')
  await page.waitForSelector('.tiles .tv-big', { timeout: 30_000 })
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+k' : 'Control+k')
  const palette = page.locator('.palette')
  await expect(palette).toBeVisible()
  await palette.locator('input').fill('checking')
  const hit = palette.locator('.prow', { hasText: 'Checking' }).first()
  await expect(hit).toBeVisible({ timeout: 15_000 })
  await page.screenshot({ path: `${SHOTS}/e2e-palette.png` })
  await hit.click()
  await expect(page.locator('.regtable')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.regbal')).toContainText('$')
  expect(page.url()).toContain('register')
  await page.screenshot({ path: `${SHOTS}/e2e-register.png`, fullPage: true })
})

test('the money map accounts table opens a register', async ({ page }) => {
  await page.goto('/#map')
  await settled(page)
  const row = page.locator('.regtable tbody tr').first()
  await expect(row).toBeVisible()
  await row.click()
  await expect(page.locator('.regback')).toBeVisible({ timeout: 15_000 })
  await page.locator('.regback').click()
  await expect(page.locator('.chart-map')).toBeVisible({ timeout: 15_000 })
})

test('connections shows the demo item and syncs via the fixture seam', async ({ page }) => {
  test.setTimeout(240_000)
  await page.goto('/#connections')
  await settled(page)
  const card = page.locator('.conn', { hasText: 'demo' })
  await expect(card).toBeVisible()
  await expect(card.locator('.connstat')).toBeVisible()
  await expect(page.locator('.slotline')).toContainText('lifetime')
  await card.locator('.btn.primary', { hasText: 'Sync now' }).click()
  await expect(card.locator('.streambox')).toBeVisible()
  await expect(card.locator('.streambox')).toContainText(/sync complete|sync exited/, {
    timeout: 210_000,
  })
  await page.screenshot({ path: `${SHOTS}/e2e-connections-synced.png`, fullPage: true })
})

test('drag-drop: a statement files, imports, and verifies', async ({ page }) => {
  test.setTimeout(240_000)
  await page.goto('/#connections')
  await settled(page)
  await page.setInputFiles('.dropzone input[type=file]', UPLOAD_FIXTURE)
  const plan = page.locator('.planbox')
  await expect(plan).toBeVisible({ timeout: 30_000 })
  await expect(plan.locator('.plabel')).toContainText('OFX')
  await expect(plan.locator('.streambox')).toContainText('BLUE HERON BOOKS')
  await page.screenshot({ path: `${SHOTS}/e2e-upload-plan.png`, fullPage: true })
  await plan.locator('.btn.primary', { hasText: 'File it and import' }).click()
  await expect(plan.locator('.streambox').last()).toContainText('✓ imported and verified', {
    timeout: 210_000,
  })
  await page.screenshot({ path: `${SHOTS}/e2e-upload-done.png`, fullPage: true })
  // the feed can find the imported row once the read model refreshed
  await page.goto('/#activity')
  await page.waitForSelector('.feed', { timeout: 30_000 })
  await page.fill('.searchbox input', 'blue heron')
  await expect(page.locator('.feed li')).toHaveCount(1, { timeout: 15_000 })
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
