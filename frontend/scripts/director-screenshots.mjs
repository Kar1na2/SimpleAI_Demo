// Headless check of the director: replays each scenario's starred take (auto-confirm) at 1440x900
// and screenshots the key moments. Needs `make backend` + `make frontend`.
//
//   make director-screenshots          # or: cd frontend && node scripts/director-screenshots.mjs
//
// Output: frontend/public/screenshots/director-*.png (gitignored; browse at /dev/screenshots).
// Exits 1 on any console error, page error or timeout. Also checks that times render in the
// browser's timezone (a second context in Asia/Seoul; WSL runs on Pacific time).
import { mkdirSync } from 'node:fs'
import { chromium } from 'playwright'

const API = process.env.API ?? 'http://127.0.0.1:8000'
const WEB = process.env.WEB ?? `http://localhost:${process.env.FRONTEND_PORT ?? 5180}`
const OUT = new URL('../public/screenshots/', import.meta.url).pathname
const WAIT = { timeout: 180_000 }
mkdirSync(OUT, { recursive: true })

const problems = []
const taken = []

async function starredTake(scenario) {
  const takes = await (await fetch(`${API}/api/replay/takes`)).json()
  const t = takes.find((x) => x.scenario === scenario && x.starred) ?? takes.find((x) => x.scenario === scenario)
  if (!t) throw new Error(`no take for ${scenario}`)
  return t
}

async function shot(page, name, settle = 400) {
  await page.waitForTimeout(settle)
  await page.screenshot({ path: `${OUT}/${name}` })
  taken.push(name)
  console.log(`  📸 ${name}`)
}

async function checkTimezone(browser, linkPath) {
  // Same link page, viewer in Seoul (WSL itself runs on Pacific time): the header time must be the Seoul
  // wall clock of link_created_at, i.e. formatted in the browser, not on the server.
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'Asia/Seoul' })
  const page = await ctx.newPage()
  const snap = await (await fetch(`${API}${linkPath.replace('/s/', '/api/s/')}`)).json()
  await page.goto(WEB + linkPath)
  const text = await page.getByTestId('link-created').innerText()
  const expected = new Date(snap.link_created_at).toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Seoul',
  })
  const ok = text.endsWith(expected)
  console.log(`  timezone: "${text}" vs Seoul ${expected} -> ${ok ? 'OK (browser timezone)' : 'MISMATCH'}`)
  if (!ok) problems.push(`link page time not in browser timezone: ${text} (expected ${expected})`)
  await ctx.close()
}

async function run(browser, scenario) {
  const take = await starredTake(scenario)
  console.log(`${scenario}: take ${take.run} (replay ${take.replay_s}s)`)
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  page.on('console', (m) => m.type() === 'error' && problems.push(`[${scenario}] console error: ${m.text()}`))
  page.on('pageerror', (e) => problems.push(`[${scenario}] page error: ${e.message}`))
  // Headless has no speakers, so prove playback by the clips the director actually fetched.
  const clips = new Set()
  page.on('response', (r) => {
    if (r.url().includes('/api/audio/') && r.status() < 400) clips.add(new URL(r.url()).pathname)
  })
  await page.goto(`${WEB}/director?scenario=${scenario}&mode=replay&take=${encodeURIComponent(take.run)}&autoconfirm=1`)
  await page.getByTestId('take-picker').waitFor(WAIT)
  const t0 = Date.now()
  await page.getByTestId('start').click()
  const frame = page.frameLocator('[data-testid="link-iframe"]')
  const n = (i, s) => `director-${scenario}-0${i}-${s}.png`

  await page.locator('[data-testid="step-minted"][data-done="true"]').waitFor(WAIT)
  await shot(page, n(1, 'connected-link-minted'), 250)
  await page.getByTestId('sms-bubble').waitFor(WAIT)
  await shot(page, n(2, 'sms-bubble'))
  await page.locator('[data-testid="step-opened"][data-done="true"]').waitFor(WAIT)
  await page.getByTestId('waiting-state').waitFor({ state: 'detached', ...WAIT })
  await shot(page, n(3, 'link-opened'))
  const linkPath = new URL(await page.getByTestId('link-iframe').getAttribute('src'), WEB).pathname

  if (scenario === 'order') {
    const qty = (q) => frame.locator('[data-testid="cart-line-FP-RIBEYE-DA16"] [data-testid="qty"]', { hasText: new RegExp(`^${q}$`) })
    await qty(10).waitFor(WAIT)
    await shot(page, n(4, 'mishear-10x-cart'))
    await frame.getByTestId('corrected-tag').waitFor(WAIT)
    await qty(2).waitFor(WAIT)
    await shot(page, n(5, 'correction'))
  } else {
    await frame.getByTestId('cal-chip-2026-12-05').waitFor(WAIT)
    await shot(page, n(4, 'saturday-calendar'))
    await frame.getByTestId('slot-chips-2026-12-04').waitFor(WAIT)
    await shot(page, n(5, 'friday-slots'))
  }
  await checkTimezone(browser, linkPath)
  await frame.getByTestId('confirm-review').waitFor(WAIT)
  await shot(page, n(6, 'confirm-tab'))
  await frame.getByTestId('expired').waitFor(WAIT)
  await shot(page, n(7, 'expired'), 600)
  console.log(`  replay wall time: ${((Date.now() - t0) / 1000).toFixed(1)}s (incl. 3 s countdown)`)
  const turns = await page.getByTestId('timeline').count() // director still mounted
  console.log(`  audio clips fetched by the director: ${clips.size}`)
  if (clips.size < 10 || !turns) problems.push(`[${scenario}] only ${clips.size} audio clips were fetched`)
  await ctx.close()
}

const browser = await chromium.launch() // headless
try {
  for (const s of (process.env.SCENARIOS ?? 'order,reservation').split(',')) await run(browser, s)
} catch (e) {
  problems.push(`run failed: ${e.message}`)
} finally {
  await browser.close()
}
console.log(`\n${taken.length} screenshots in frontend/public/screenshots/`)
if (problems.length) {
  console.log(`${problems.length} problem(s):\n  ${problems.join('\n  ')}`)
  process.exit(1)
}
console.log('0 console errors')
