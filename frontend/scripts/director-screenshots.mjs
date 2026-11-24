// Headless check of the director: replays each scenario's starred take (auto-confirm) at 1440x900
// and screenshots the key moments. Needs `make backend` + `make frontend`.
//
//   make director-screenshots          # or: cd frontend && node scripts/director-screenshots.mjs
//
// Output: frontend/public/screenshots/director-*.png (gitignored; browse at /dev/screenshots).
// Exits 1 on any console error, page error, timeout, or "100." / ":5180" in the rendered text
// (director + iframe at every shot, plus the Agent actions and Notes tabs). Also checks that times render in the
// browser's timezone (a second context in Asia/Seoul; WSL runs on Pacific time), and, frame by frame,
// that no timeline step turns done (or green) before its event arrives (see frameMonitor).
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

// No host or port may appear on screen: the rendered text (plus tab title and tooltip-like
// attributes) of the director and of the link page inside the iframe is grepped at every shot.
const LEAKS = ['100.', ':5180']
let textChecks = 0

async function renderedText(page) {
  const parts = []
  for (const f of page.frames()) {
    parts.push(
      await f
        .evaluate(() => {
          const attrs = ['title', 'aria-label', 'alt', 'placeholder']
          const els = [...document.querySelectorAll(attrs.map((a) => `[${a}]`).join(','))]
          return [document.title, document.body?.innerText ?? '', ...els.flatMap((el) => attrs.map((a) => el.getAttribute(a) ?? ''))].join('\n')
        })
        .catch(() => ''),
    )
  }
  return parts.join('\n')
}

async function shot(page, name, settle = 400) {
  await page.waitForTimeout(settle)
  await page.screenshot({ path: `${OUT}/${name}` })
  const text = await renderedText(page)
  for (const leak of LEAKS) {
    const i = text.indexOf(leak)
    if (i >= 0) problems.push(`${name}: rendered text contains "${leak}": …${text.slice(Math.max(0, i - 30), i + 30).replace(/\n/g, ' ⏎ ')}…`)
  }
  textChecks++
  taken.push(name)
  const sms = name.includes('sms-bubble') ? ` · SMS: "${await page.getByTestId('sms-bubble').innerText()}"` : ''
  console.log(`  📸 ${name} (${page.frames().length} frame(s) text-checked)${sms.replace(/\n/g, ' / ')}`)
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

  // The screenshots show the Live view; grep the other tabs' rendered text for the host too.
  for (const tab of ['actions', 'notes']) {
    await page.getByTestId(`tab-${tab}`).click()
    await page.getByTestId(`panel-${tab}`).waitFor()
    const body = await renderedText(page)
    const hits = LEAKS.filter((l) => body.includes(l))
    const texted = tab === 'actions' ? (body.match(/Link texted to \S+/) ?? ['(no "Link texted" entry)'])[0] : null
    console.log(`  ${tab} tab: ${hits.length ? `contains ${hits.join(', ')}` : 'no host'}${texted ? ` · "${texted}"` : ''}`)
    if (hits.length) problems.push(`link page ${tab} tab contains ${hits.join(', ')}`)
    if (texted?.startsWith('(')) problems.push('agent actions tab has no "Link texted to" entry')
    textChecks++
  }
  await ctx.close()
}

// --- frame-by-frame monitor ---------------------------------------------------------------------
// Injected into the director page (not the iframe) before its own scripts. It timestamps every call
// event as the websocket delivers it, and on every animation frame records the timeline's state:
// when each step first shows as done, how many frames it spent not done / "waiting", and any frame
// where a step that is not done shows green (any color, background, border, shadow, fill or stroke
// with a green hue). It also samples the pre-link captions: font size, highlighted line, text vs
// transcript.turn text_said, and the panel's fade-out.
function frameMonitor() {
  if (window !== window.top) return
  const log = {
    msgs: [], frames: 0, firstDone: {}, notDone: {}, waiting: {}, green: [], undone: [],
    captionFrames: 0, playingFrames: 0, captionMinPx: Infinity, captionMismatch: [], fadeFrames: 0,
  }
  window.__monitor = log
  const said = new Map()
  const NativeWS = window.WebSocket
  window.WebSocket = class extends NativeWS {
    constructor(...args) {
      super(...args)
      // Registered before the app's onmessage, so this timestamp precedes any render it causes.
      this.addEventListener('message', (m) => {
        let e
        try { e = JSON.parse(m.data) } catch { return }
        if (!e || e.seq == null || !e.type) return
        const p = e.payload ?? {}
        log.msgs.push({ t: performance.now(), seq: e.seq, type: e.type, status: p.status, marks: p.marks, beat: p.beat_id })
        if (e.type === 'transcript.turn') said.set(String(p.turn_id), String(p.text_said ?? ''))
      })
    }
  }
  const num = /-?\d*\.?\d+(?:e-?\d+)?/g
  const green = (c) => {
    const v = (c.match(num) ?? []).map(Number)
    let h, chroma, a
    if (c.startsWith('rgb') || c.startsWith('color(srgb')) {
      const k = c.startsWith('color(') ? 255 : 1
      const [r, g, b] = v.map((x) => x * k)
      a = v[3] ?? 1
      const max = Math.max(r, g, b), min = Math.min(r, g, b)
      chroma = (max - min) / 255
      if (max === min) return false
      h = max === r ? 60 * (((g - b) / (max - min)) % 6) : max === g ? 60 * ((b - r) / (max - min) + 2) : 60 * ((r - g) / (max - min) + 4)
      if (h < 0) h += 360
      return a > 0.02 && chroma > 0.15 && h >= 85 && h <= 175
    }
    if (c.startsWith('oklch')) [, chroma, h, a = 1] = v
    else if (c.startsWith('oklab')) {
      chroma = Math.hypot(v[1], v[2])
      h = (Math.atan2(v[2], v[1]) * 180) / Math.PI
      if (h < 0) h += 360
      a = v[3] ?? 1
    } else return false
    return a > 0.02 && chroma > 0.04 && h >= 110 && h <= 185
  }
  const colors = (css) => css.match(/(?:rgba?|oklch|oklab|color)\([^)]*\)/g) ?? []
  const tick = () => {
    const t = performance.now()
    log.frames++
    for (const li of document.querySelectorAll('[data-testid^="step-"]')) {
      const key = li.dataset.testid.slice(5)
      if (li.dataset.done === 'true') {
        log.firstDone[key] ??= t
        continue
      }
      if (key in log.firstDone) log.undone.push(key)
      log.notDone[key] = (log.notDone[key] ?? 0) + 1
      if (li.dataset.state === 'waiting') log.waiting[key] = (log.waiting[key] ?? 0) + 1
      for (const el of [li, ...li.querySelectorAll('*')]) {
        const cs = getComputedStyle(el)
        const hit = colors([cs.color, cs.backgroundColor, cs.borderTopColor, cs.boxShadow, cs.fill, cs.stroke, cs.outlineColor].join(' ')).find(green)
        if (hit) {
          if (log.green.length < 10) log.green.push({ key, t, color: hit, el: el.tagName })
          break
        }
      }
    }
    const lines = document.querySelectorAll('[data-testid="caption-line"]')
    if (lines.length) log.captionFrames++
    if ([...lines].some((l) => l.dataset.playing === 'true')) log.playingFrames++
    for (const line of lines) {
      const text = line.querySelector('[data-testid="caption-text"]')
      log.captionMinPx = Math.min(log.captionMinPx, parseFloat(getComputedStyle(text).fontSize), parseFloat(getComputedStyle(line.firstElementChild).fontSize))
      const want = said.get(line.dataset.turnId)
      if (want != null && text.textContent !== want && log.captionMismatch.length < 5) log.captionMismatch.push({ turn: line.dataset.turnId, shown: text.textContent, said: want })
    }
    const panel = document.querySelector('[data-testid="waiting-state"]')
    if (panel) {
      const o = parseFloat(getComputedStyle(panel).opacity)
      if (o > 0.05 && o < 0.95) log.fadeFrames++
    }
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}

// Which event completes each timeline step (mirrors STEPS in Director.tsx).
const STEP_EVENTS = {
  dialing: (x) => x.type === 'call.status' && x.status === 'dialing',
  ringing: (x) => x.type === 'call.status' && x.status === 'ringing',
  connected: (x) => x.type === 'call.status' && x.status === 'connected',
  minted: (x) => x.type === 'link.minted',
  yes: (x) => x.type === 'transcript.turn' && x.marks === 'link_accepted',
  sent: (x) => x.type === 'link.sent',
  opened: (x) => x.type === 'link.opened',
  requested: (x) => x.type === 'confirm.requested',
  confirmed: (x) => x.type === 'confirm.received',
  ended: (x) => x.type === 'call.status' && x.status === 'ended',
  revoked: (x) => x.type === 'link.revoked',
}
const LABEL = { yes: 'Customer said yes', confirmed: 'Confirmed' }

function reportFrames(scenario, m) {
  const tag = `[${scenario}]`
  const t0 = m.msgs[0]?.t ?? 0
  const s = (t) => `${((t - t0) / 1000).toFixed(2)}s`
  let maxLag = 0
  let steps = 0
  for (const [key, pred] of Object.entries(STEP_EVENTS)) {
    const msg = m.msgs.find(pred)
    const done = m.firstDone[key]
    if (!msg && done == null) continue // e.g. no Dialing step on an inbound call
    steps++
    if (!msg) problems.push(`${tag} step ${key} shown done but its event never arrived`)
    else if (done == null) problems.push(`${tag} step ${key}: event arrived but the step never showed done`)
    else if (done < msg.t) problems.push(`${tag} step ${key} shown done ${(msg.t - done).toFixed(0)} ms BEFORE its event arrived`)
    else maxLag = Math.max(maxLag, done - msg.t)
  }
  const focus = scenario === 'order' ? 'yes' : 'confirmed'
  const msg = m.msgs.find(STEP_EVENTS[focus])
  const greenHere = m.green.filter((g) => g.key === focus).length
  const what = focus === 'yes' ? `${msg?.beat} turn (seq ${msg?.seq}, marks=link_accepted)` : `confirm.received (seq ${msg?.seq})`
  console.log(
    `  frames: "${LABEL[focus]}" grey for ${m.notDone[focus] ?? 0} frames${m.waiting[focus] ? ` (${m.waiting[focus]} of them the grey waiting pulse)` : ''}, ` +
      `${greenHere ? `GREEN in ${greenHere}` : 'never green'}; ${what} arrived at ${s(msg?.t ?? 0)}, step checked at ${s(m.firstDone[focus] ?? 0)} ` +
      `(+${((m.firstDone[focus] ?? 0) - (msg?.t ?? 0)).toFixed(0)} ms, next frame)`,
  )
  if (scenario === 'order' && msg?.beat !== 'c3') problems.push(`${tag} link_accepted came from ${msg?.beat}, expected c3`)
  console.log(
    `  frames: ${m.frames} sampled; ${steps} steps each checked only after its event (max lag ${maxLag.toFixed(0)} ms); ` +
      `${m.green.length} frames with green on a step that is not done${m.undone.length ? `; UN-DONE: ${m.undone.join(',')}` : ''}`,
  )
  if (m.green.length) problems.push(`${tag} green on not-done steps: ${JSON.stringify(m.green.slice(0, 3))}`)
  if (m.undone.length) problems.push(`${tag} steps went from done back to not done: ${m.undone.join(',')}`)
  console.log(
    `  captions: ${m.captionFrames} frames, line highlighted in ${m.playingFrames}, smallest text ${m.captionMinPx}px, ` +
      `${m.captionMismatch.length} lines differing from text_said, panel fade-out over ${m.fadeFrames} frames`,
  )
  if (!m.captionFrames || !m.playingFrames) problems.push(`${tag} captions never showed a highlighted line`)
  if (m.captionMinPx < 15) problems.push(`${tag} caption text below 15px (${m.captionMinPx}px)`)
  if (m.captionMismatch.length) problems.push(`${tag} caption text != text_said: ${JSON.stringify(m.captionMismatch[0])}`)
  if (m.fadeFrames < 3) problems.push(`${tag} the pre-link panel did not fade out (${m.fadeFrames} mid-fade frames)`)
}

async function run(browser, scenario) {
  const take = await starredTake(scenario)
  console.log(`${scenario}: take ${take.run} (replay ${take.replay_s}s)`)
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  await ctx.addInitScript(frameMonitor)
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
  // Self-test of the green detector: three green not-done steps (oklch, rgb, color-mix/oklab) must be
  // flagged and a grey one must not. Removed before Start.
  const flagged = await page.evaluate(async () => {
    const ol = document.createElement('ol')
    const li = (k, style) => `<li data-testid="step-selftest-${k}" data-done="false"><span style="${style}">x</span></li>`
    ol.innerHTML =
      li('oklch', 'background: oklch(0.696 0.17 162.48)') +
      li('rgb', 'color: rgb(16 185 129 / 0.5)') +
      li('mix', 'background: color-mix(in oklab, oklch(0.696 0.17 162.48) 30%, transparent)') +
      li('grey', 'color: oklch(0.709 0.01 56.259); background: rgb(231 229 228)')
    document.body.append(ol)
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
    ol.remove()
    const m = window.__monitor
    const keys = [...new Set(m.green.map((g) => g.key))].sort()
    m.green = []
    for (const k of Object.keys(m.notDone)) if (k.startsWith('selftest')) delete m.notDone[k]
    return keys.join(',')
  })
  const selfOk = flagged === 'selftest-mix,selftest-oklch,selftest-rgb'
  console.log(`  green detector self-test: flagged [${flagged}] ${selfOk ? 'OK' : 'FAILED'}`)
  if (!selfOk) problems.push(`[${scenario}] green detector self-test failed: ${flagged}`)
  const t0 = Date.now()
  await page.getByTestId('start').click()
  const frame = page.frameLocator('[data-testid="link-iframe"]')
  const n = (i, s) => `director-${scenario}-0${i}-${s}.png`

  await page.locator('[data-testid="step-minted"][data-done="true"]').waitFor(WAIT)
  await shot(page, n(1, 'connected-link-minted'), 250)
  // Pre-link captions: 3 lines, one of them highlighted while its audio plays.
  await page.waitForFunction(
    () => document.querySelectorAll('[data-testid="caption-line"]').length >= 3 && document.querySelector('[data-testid="caption-line"][data-playing="true"]'),
    null,
    WAIT,
  )
  await shot(page, n(2, 'live-captions'), 500)
  await page.getByTestId('sms-bubble').waitFor(WAIT)
  await shot(page, n(3, 'sms-bubble'))
  await page.locator('[data-testid="step-opened"][data-done="true"]').waitFor(WAIT)
  await page.getByTestId('waiting-state').waitFor({ state: 'detached', ...WAIT })
  await shot(page, n(4, 'link-opened'))
  const linkPath = new URL(await page.getByTestId('link-iframe').getAttribute('src'), WEB).pathname

  if (scenario === 'order') {
    const qty = (q) => frame.locator('[data-testid="cart-line-FP-RIBEYE-DA16"] [data-testid="qty"]', { hasText: new RegExp(`^${q}$`) })
    await qty(10).waitFor(WAIT)
    await shot(page, n(5, 'mishear-10x-cart'))
    await frame.getByTestId('corrected-tag').waitFor(WAIT)
    await qty(2).waitFor(WAIT)
    await shot(page, n(6, 'correction'))
  } else {
    await frame.getByTestId('cal-chip-2026-12-05').waitFor(WAIT)
    await shot(page, n(5, 'saturday-calendar'))
    await frame.getByTestId('slot-chips-2026-12-04').waitFor(WAIT)
    await shot(page, n(6, 'friday-slots'))
  }
  await checkTimezone(browser, linkPath)
  await frame.getByTestId('confirm-review').waitFor(WAIT)
  await shot(page, n(7, 'confirm-tab'))
  await frame.getByTestId('expired').waitFor(WAIT)
  await shot(page, n(8, 'expired'), 600)
  console.log(`  replay wall time: ${((Date.now() - t0) / 1000).toFixed(1)}s (incl. 3 s countdown)`)
  const turns = await page.getByTestId('timeline').count() // director still mounted
  console.log(`  audio clips fetched by the director: ${clips.size}`)
  if (clips.size < 10 || !turns) problems.push(`[${scenario}] only ${clips.size} audio clips were fetched`)
  reportFrames(scenario, await page.evaluate(() => window.__monitor))
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
console.log(`host check: rendered text of ${textChecks} views (${taken.length} shots + ${textChecks - taken.length} Agent actions/Notes tab views) grepped for ${LEAKS.map((l) => `"${l}"`).join(' and ')}`)
if (problems.length) {
  console.log(`${problems.length} problem(s):\n  ${problems.join('\n  ')}`)
  process.exit(1)
}
console.log('0 console errors')
