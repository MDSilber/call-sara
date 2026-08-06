/** Visual pass driver: load each room, screenshot light + dark. Not the
 * assertion suite (app.spec.ts is) — this exists so a human (or Claude)
 * can LOOK at every room quickly. Shots land in $SARA_SHOTS or e2e/shots. */
import { test } from '@playwright/test'
import type { Page } from '@playwright/test'

const SHOTS = process.env.SARA_SHOTS ?? 'e2e/shots'
const ROOMS: [string, string][] = [
  ['spending', 'Spending'], ['activity', 'Activity'], ['map', 'Money map'],
  ['investments', 'Investments'], ['goals', 'Goals'], ['autopilot', 'Autopilot'],
]

async function settled(page: Page): Promise<void> {
  await page.waitForFunction(() => !document.querySelector('.room .skeleton'), undefined, { timeout: 30_000 })
  await page.waitForTimeout(700)
}

test('screenshot every room', async ({ page }) => {
  test.setTimeout(240_000)
  await page.goto('/')
  await page.waitForSelector('.tiles .tv-big', { timeout: 30_000 })
  await settled(page)
  await page.screenshot({ path: `${SHOTS}/glance.png`, fullPage: false })
  for (const [id, label] of ROOMS) {
    await page.click(`.tab:has-text("${label}")`)
    await settled(page)
    await page.screenshot({ path: `${SHOTS}/room-${id}.png`, fullPage: true })
  }
  // dark theme: cycle auto -> light -> dark
  await page.click('.themebtn')
  await page.click('.themebtn')
  await page.click('.tab:has-text("Spending")')
  await settled(page)
  await page.screenshot({ path: `${SHOTS}/dark-spending.png`, fullPage: true })
  await page.click('.tab:has-text("Money map")')
  await settled(page)
  await page.screenshot({ path: `${SHOTS}/dark-map.png`, fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.click('.tab:has-text("Spending")')
  await settled(page)
  await page.screenshot({ path: `${SHOTS}/phone-glance.png`, fullPage: true })
})
