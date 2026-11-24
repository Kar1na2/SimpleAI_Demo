// Drives both scenarios through the real stack and screenshots the link page (headless only).
//
//   make backend   # terminal 1
//   make frontend  # terminal 2
//   make screenshots                        # SPEED=1.5 by default
//
// Output: frontend/public/screenshots/ (gitignored), browsable at /dev/screenshots.
// Link URLs from the dev endpoint use PUBLIC_BASE_URL (the Tailscale IP). Inside WSL that address
// only answers once expose-demo.ps1 has run, so the browser here opens the same path on WEB.
//
// Each call starts with POST /api/calls/start (auto mode). The page is opened once link.sent has
// happened (as a customer would), and every screenshot waits for a real DOM state, not a timer.
// Exits 1 on any console error, page error or timeout.
import { mkdirSync } from 'node:fs'
import { chromium } from 'playwright'

const API = process.env.API ?? 'http://127.0.0.1:8000'
const SPEED = Number(process.env.SPEED ?? 1.5)
const WEB = process.env.WEB ?? `http://localhost:${process.env.FRONTEND_PORT ?? 5180}`
const OUT = new URL('../public/screenshots/', import.meta.url).pathname
const local = (url) => WEB + new URL(url).pathname
const EXTRA = process.env.EXTRA_DIR // optional: Notes / Agent actions shots for review
const WAIT = { timeout: 150_000 }
const DESKTOP = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 }
const MOBILE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }

mkdirSync(OUT, { recursive: true })
if (EXTRA) mkdirSync(EXTRA, { recursive: true })
const problems = []
const taken = []
let sawTyping = false

async function startCall(scenario) {
  const r = await fetch(`${API}/api/calls/start`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ scenario, mode: 'auto', speed: SPEED, tts: false }), // link page only: no voices needed
  })
  if (r.status !== 201) throw new Error(`start ${scenario}: ${r.status} ${await r.text()}`)
  return (await r.json()).call_id
}

async function linkFor(callId) {
  for (let i = 0; i < 200; i++) {
    const r = await fetch(`${API}/api/dev/calls/${callId}/link`)
    if (r.ok) return (await r.json()).url
    await new Promise((res) => setTimeout(res, 100))
  }
  throw new Error('no link URL after 20s')
}

/** Resolves when the call has emitted `type` (the dev socket replays history first). */
function waitForEvent(callId, type, ms = 120_000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${API.replace('http', 'ws')}/ws/calls/${callId}`)
    const timer = setTimeout(() => (ws.close(), reject(new Error(`timeout waiting for ${type}`))), ms)
    ws.addEventListener('message', (m) => {
      if (JSON.parse(m.data).type === type) {
        clearTimeout(timer)
        ws.close()
        resolve()
      }
    })
  })
}

function watch(page, label) {
  page.on('console', (m) => m.type() === 'error' && problems.push(`[${label}] console error: ${m.text()}`))
  page.on('pageerror', (e) => problems.push(`[${label}] page error: ${e.message}`))
}

async function shot(page, name, dir = OUT) {
  await page.waitForTimeout(450) // let 150-250ms transitions settle
  await page.screenshot({ path: `${dir}/${name}` })
  if (dir === OUT) taken.push(name)
  console.log(`  📸 ${name}`)
}

async function openLink(browser, url, opts, label) {
  const ctx = await browser.newContext(opts)
  const page = await ctx.newPage()
  watch(page, label)
  await page.goto(local(url))
  await page.getByTestId('link-page').waitFor(WAIT)
  return { ctx, page }
}

async function extras(browser, url, scenario) {
  if (!EXTRA) return
  const { ctx, page } = await openLink(browser, url, DESKTOP, `${scenario}-extras`)
  await page.getByTestId('tab-notes').click()
  await shot(page, `${scenario}-notes.png`, EXTRA)
  await page.getByTestId('tab-actions').click()
  await shot(page, `${scenario}-actions.png`, EXTRA)
  await ctx.close()
}

async function order(browser) {
  console.log('order')
  const id = await startCall('order')
  const url = await linkFor(id)
  await waitForEvent(id, 'link.sent')
  const { ctx, page } = await openLink(browser, url, DESKTOP, 'order')
  // The agent typing indicator should show between a customer turn and the agent's reply.
  page.getByTestId('typing').waitFor(WAIT).then(() => (sawTyping = true), () => {})
  const qty = (sku, n) => page.locator(`[data-testid="cart-line-${sku}"] [data-testid="qty"]`, { hasText: new RegExp(`^${n}$`) })

  await shot(page, 'order-01-link-opened.png')
  await qty('FP-RIBEYE-DA16', 10).waitFor(WAIT)
  await shot(page, 'order-02-ten-ribeyes.png')
  await page.getByTestId('corrected-tag').waitFor(WAIT)
  await qty('FP-RIBEYE-DA16', 2).waitFor(WAIT)
  await shot(page, 'order-03-corrected.png')

  const mobile = (async () => {
    const m = await openLink(browser, url, MOBILE, 'mobile')
    await m.page.getByTestId('cart').waitFor(WAIT)
    await shot(m.page, 'mobile-01-mid-call.png')
    await m.ctx.close()
  })()

  await page.getByTestId('cart-line-FP-BUTTER-GH').waitFor(WAIT)
  await shot(page, 'order-04-butter-added.png')
  const extra = extras(browser, url, 'order')
  await page.getByTestId('confirm-review').waitFor(WAIT)
  await shot(page, 'order-05-confirm-tab.png')
  await page.getByTestId('expired').waitFor(WAIT)
  await shot(page, 'order-06-expired.png')
  await Promise.all([mobile, extra])
  await ctx.close()
}

async function reservation(browser) {
  console.log('reservation')
  const id = await startCall('reservation')
  const url = await linkFor(id)
  await waitForEvent(id, 'link.sent')
  const { ctx, page } = await openLink(browser, url, DESKTOP, 'reservation')

  await page.getByTestId('cal-chip-2026-12-05').waitFor(WAIT)
  await shot(page, 'reservation-01-saturday-dec-5.png')
  await page.getByTestId('detail-party').filter({ hasText: 'Party of 5' }).waitFor(WAIT)
  await shot(page, 'reservation-02-party-of-5.png')
  await page.getByTestId('slot-chips-2026-12-04').waitFor(WAIT)
  await shot(page, 'reservation-03-friday-slots.png')
  const extra = extras(browser, url, 'reservation')
  await page.getByTestId('confirm-review').waitFor(WAIT)
  await shot(page, 'reservation-04-confirm-tab.png')
  await page.getByTestId('expired').waitFor(WAIT)
  await shot(page, 'reservation-05-expired.png')
  await extra
  await ctx.close()
}

const browser = await chromium.launch()
try {
  await order(browser)
  await reservation(browser)
} catch (e) {
  problems.push(`run failed: ${e.message}`)
} finally {
  await browser.close()
}
console.log(`\n${taken.length} screenshots in frontend/public/screenshots/ (view at /dev/screenshots)`)
console.log(`typing indicator seen during the order call: ${sawTyping ? 'yes' : 'NO'}`)
if (!sawTyping) problems.push('typing indicator never appeared')
if (problems.length) {
  console.log(`${problems.length} problem(s):\n  ${problems.join('\n  ')}`)
  process.exit(1)
}
console.log('0 console errors')
