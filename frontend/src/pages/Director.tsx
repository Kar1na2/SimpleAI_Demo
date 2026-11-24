// Director view (/director): what gets recorded. Left: the call console. Right: the customer's
// link page in a scaled iframe. Audio (both voices, ringback, connect click) plays here only.
//
// URL options: ?scenario=order|reservation&mode=live|replay&take=<run file>&autoconfirm=1
// (autoconfirm is for headless checks; recording uses a real click inside the iframe).
import { Check, Circle, LoaderCircle, Play, Radio, Star } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { CustomerScreen } from '../components/director/CustomerScreen.tsx'
import { AudioEngine, type Speaking } from '../lib/director/audioEngine.ts'
import { mmss, timeOfDay } from '../lib/link/format.ts'
import { useNow } from '../lib/link/hooks.ts'
import { type CallEvent, useCallEvents } from '../lib/useCallEvents.ts'

type ScenarioId = 'order' | 'reservation'
type Mode = 'live' | 'replay'
type Phase = 'idle' | 'countdown' | 'starting' | 'running' | 'error'
type Take = { run: string; scenario: ScenarioId; started_at: string; replay_s: number; nudges: number; starred: boolean }
type CallInfo = {
  brand: string | null
  agent_name: string | null
  direction: 'inbound' | 'outbound'
  customer_name: string | null
  customer_phone_last4: string | null
  link_url: string | null
  kind: 'live' | 'replay' | null
}

const SCENARIOS: { id: ScenarioId; label: string }[] = [
  { id: 'order', label: 'Order · outbound' },
  { id: 'reservation', label: 'Reservation · inbound' },
]

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const takeLabel = (t: Take) =>
  `${t.starred ? '★ ' : ''}${new Date(t.started_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · ${mmss(t.replay_s * 1000)}`

// --- status timeline ----------------------------------------------------------------------------

type Step = { key: string; label: string; match: (e: CallEvent) => boolean; outboundOnly?: boolean; key_moment?: boolean }
const status = (s: string) => (e: CallEvent) => e.type === 'call.status' && e.payload.status === s
const STEPS: Step[] = [
  { key: 'dialing', label: 'Dialing', match: status('dialing'), outboundOnly: true },
  { key: 'ringing', label: 'Ringing', match: status('ringing') },
  { key: 'connected', label: 'Connected', match: status('connected') },
  { key: 'minted', label: 'Secure link minted', match: (e) => e.type === 'link.minted', key_moment: true },
  { key: 'yes', label: 'Customer said yes', match: (e) => e.type === 'transcript.turn' && e.payload.marks === 'link_accepted' },
  { key: 'sent', label: 'Link texted', match: (e) => e.type === 'link.sent' },
  { key: 'opened', label: 'Link opened', match: (e) => e.type === 'link.opened' },
  { key: 'requested', label: 'Confirmation requested', match: (e) => e.type === 'confirm.requested' },
  { key: 'confirmed', label: 'Confirmed', match: (e) => e.type === 'confirm.received' },
  { key: 'ended', label: 'Call ended', match: status('ended') },
  { key: 'revoked', label: 'Link revoked', match: (e) => e.type === 'link.revoked' },
]

// Three states. done: the event happened (check mark + its time; the only state that may use green).
// waiting: we're blocked on this step (e.g. the confirm click): a subtle grey pulse. upcoming: plain grey,
// including the very next step.
function Timeline({ events, direction, waitingFor }: { events: CallEvent[]; direction: string | null; waitingFor: string | null }) {
  const steps = STEPS.filter((s) => !s.outboundOnly || direction !== 'inbound')
  const hits = steps.map((s) => events.find(s.match))
  return (
    <ol className="relative" data-testid="timeline">
      {steps.map((s, i) => {
        const hit = hits[i]
        const waiting = !hit && s.key === waitingFor
        return (
          <li
            key={s.key}
            data-testid={`step-${s.key}`}
            data-done={Boolean(hit)}
            data-state={hit ? 'done' : waiting ? 'waiting' : 'upcoming'}
            className={`relative -mx-2 flex items-center gap-3 rounded-md px-2 py-[5px] ${hit && s.key_moment ? 'animate-flash-key' : ''}`}
          >
            {i < steps.length - 1 && (
              <span className={`absolute top-[18px] left-[15px] h-[calc(100%-6px)] w-px ${hits[i + 1] ? 'bg-stone-800' : 'bg-stone-200'}`} aria-hidden />
            )}
            <span className="relative z-10 flex h-[14px] w-[14px] shrink-0 items-center justify-center">
              {hit ? (
                <span className="flex h-[14px] w-[14px] items-center justify-center rounded-full bg-stone-800 text-white">
                  <Check size={9} strokeWidth={3.5} />
                </span>
              ) : waiting ? (
                <span className="relative flex h-[10px] w-[10px]">
                  <span className="absolute inset-0 rounded-full bg-stone-300 animate-live-ping" />
                  <span className="relative h-[10px] w-[10px] rounded-full bg-stone-400" />
                </span>
              ) : (
                <Circle size={10} className="text-stone-300" />
              )}
            </span>
            <span className={`flex-1 text-[13px] ${hit ? 'text-stone-900' : waiting ? 'text-stone-500' : 'text-stone-400'}`}>{s.label}</span>
            <span className="text-[11px] text-stone-400 tabular-nums">{hit ? timeOfDay(hit.created_at) : ''}</span>
          </li>
        )
      })}
    </ol>
  )
}

// --- now speaking ---------------------------------------------------------------------------------

function NowSpeaking({ speaking, engine }: { speaking: Speaking; engine: AudioEngine }) {
  const bars = useRef<(HTMLSpanElement | null)[]>([])
  useEffect(() => {
    let raf = 0
    const shape = [0.55, 0.85, 1, 0.8, 0.5]
    const tick = () => {
      const level = engine.level()
      bars.current.forEach((b, i) => {
        if (b) b.style.transform = `scaleY(${Math.max(0.12, Math.min(1, level * shape[i] * (0.8 + Math.random() * 0.4)))})`
      })
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [engine])
  return (
    <div className="flex items-center justify-between rounded-lg border border-black/[0.07] px-3 py-2.5" data-testid="now-speaking">
      <div className="min-w-0">
        <div className="text-[11px] font-medium tracking-wide text-stone-400 uppercase">Now speaking</div>
        <div className="truncate text-sm font-medium text-stone-900">
          {speaking ? `${speaking.name}${speaking.speaker === 'agent' ? ' (agent)' : ' (customer)'}` : '—'}
        </div>
      </div>
      <div className="flex h-6 items-center gap-[3px]" aria-hidden>
        {[0, 1, 2, 3, 4].map((i) => (
          <span
            key={i}
            ref={(el) => {
              bars.current[i] = el
            }}
            className={`h-6 w-[3px] origin-center rounded-full transition-transform duration-75 ${!speaking ? 'bg-stone-300' : speaking.speaker === 'customer' ? 'bg-sky-500' : 'bg-emerald-600'}`}
            style={{ transform: 'scaleY(0.12)' }}
          />
        ))}
      </div>
    </div>
  )
}

// --- page ------------------------------------------------------------------------------------------

export default function Director() {
  const [params] = useSearchParams()
  const autoConfirm = params.get('autoconfirm') === '1'
  const [scenario, setScenario] = useState<ScenarioId>(params.get('scenario') === 'reservation' ? 'reservation' : 'order')
  const [mode, setMode] = useState<Mode>(params.get('mode') === 'replay' ? 'replay' : 'live')
  const [takes, setTakes] = useState<Take[]>([])
  const [take, setTake] = useState<string>(params.get('take') ?? '')
  const [phase, setPhase] = useState<Phase>('idle')
  const [count, setCount] = useState(3)
  const [error, setError] = useState<string | null>(null)
  const [callId, setCallId] = useState<string>()
  const [callMode, setCallMode] = useState<Mode>('live')
  const [info, setInfo] = useState<CallInfo | null>(null)
  const [speaking, setSpeaking] = useState<Speaking>(null)
  const [receivedAt, setReceivedAt] = useState<Record<string, number>>({})
  const engine = useMemo(() => new AudioEngine(), [])
  const { events } = useCallEvents(callId)
  const processed = useRef(0)
  const now = useNow(250)

  useEffect(() => {
    engine.onSpeakingChange = setSpeaking
    return () => engine.reset()
  }, [engine])

  // Takes for the take picker (passing TTS runs; starred first).
  const loadTakes = useCallback(async () => {
    const r = await fetch('/api/replay/takes')
    if (r.ok) setTakes((await r.json()) as Take[])
  }, [])
  useEffect(() => {
    loadTakes()
  }, [loadTakes])
  const scenarioTakes = takes.filter((t) => t.scenario === scenario)
  const selectedStarred = Boolean(scenarioTakes.find((t) => t.run === take)?.starred)
  useEffect(() => {
    if (mode === 'replay' && !scenarioTakes.some((t) => t.run === take)) setTake(scenarioTakes[0]?.run ?? '')
  }, [mode, scenario, takes]) // eslint-disable-line react-hooks/exhaustive-deps

  // New events: audio clips, ringback, connect click, local arrival times.
  useEffect(() => {
    const arrived: Record<string, number> = {}
    for (let i = processed.current; i < events.length; i++) {
      const e = events[i]
      const t = Date.now()
      if (e.type === 'audio.play')
        engine.enqueue({
          url: String(e.payload.url),
          name: String(e.payload.name),
          speaker: e.payload.speaker === 'customer' ? 'customer' : 'agent',
          turnId: e.payload.turn_id != null ? Number(e.payload.turn_id) : null,
        })
      if (e.type === 'call.status') {
        if (e.payload.status === 'ringing') engine.startRing()
        else engine.stopRing()
        if (e.payload.status === 'connected') engine.click()
      }
      if (['call.status', 'link.sent', 'transcript.turn', 'audio.play'].includes(e.type)) arrived[e.seq] = t
    }
    processed.current = events.length
    if (Object.keys(arrived).length) setReceivedAt((prev) => ({ ...prev, ...arrived }))
  }, [events, engine])

  // Call card info. Refetched once the token is minted: link_url is the iframe's src only, never shown as text.
  const minted = events.some((e) => e.type === 'link.minted')
  useEffect(() => {
    if (!callId) return
    fetch(`/api/dev/calls/${callId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => j && setInfo(j as CallInfo))
  }, [callId, minted])

  const start = async () => {
    setError(null)
    await engine.unlock() // the Start click is the user gesture that allows audio
    engine.reset()
    processed.current = 0
    setReceivedAt({})
    setCallId(undefined)
    setInfo(null)
    setCallMode(mode)
    setPhase('countdown')
    for (const n of [3, 2, 1]) {
      setCount(n)
      await sleep(1000)
    }
    setPhase('starting')
    const r =
      mode === 'live'
        ? await fetch('/api/calls/start', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ scenario, mode: autoConfirm ? 'auto' : 'manual', speed: 1, tts: true }),
          })
        : await fetch('/api/replay', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ run: take, max_gap_ms: 700, auto_confirm: autoConfirm }),
          })
    if (!r.ok) {
      setError(String((await r.json().catch(() => ({}))).detail ?? `start failed (${r.status})`))
      setPhase('error')
      return
    }
    setCallId(String((await r.json()).call_id))
    setPhase('running')
  }

  const starTake = async () => {
    if (!take) return
    await fetch('/api/replay/star', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ run: take }) })
    loadTakes()
  }

  // Derived call state ------------------------------------------------------------------------
  const first = (pred: (e: CallEvent) => boolean) => events.find(pred)
  const connectedEv = first(status('connected'))
  const endedEv = first(status('ended'))
  const lastStatus = [...events].reverse().find((e) => e.type === 'call.status')?.payload.status as string | undefined
  const running = phase === 'running' && !first((e) => e.type === 'link.revoked')
  const timerStart = connectedEv ? receivedAt[connectedEv.seq] : undefined
  const timerEnd = endedEv ? receivedAt[endedEv.seq] : undefined
  const callTime = timerStart ? mmss((timerEnd ?? now) - timerStart) : null

  // "Waiting for confirm click": after the customer's confirm line has been spoken, until the click.
  const confirmTurn = [...events].reverse().find((e) => e.type === 'transcript.turn' && e.payload.action === 'confirm')
  const confirmAudio = confirmTurn && events.find((e) => e.type === 'audio.play' && e.payload.turn_id === confirmTurn.payload.turn_id)
  const confirmDue = confirmTurn && receivedAt[confirmTurn.seq] != null
    ? receivedAt[confirmTurn.seq] + Number(confirmAudio?.payload.duration_ms ?? confirmTurn.payload.duration_ms ?? 0)
    : null
  const waitingForClick = running && confirmDue != null && now >= confirmDue && !first((e) => e.type === 'confirm.received')

  const sent = first((e) => e.type === 'link.sent')
  const direction = info?.direction ?? null
  const who = info?.customer_name ?? '…'
  const phone = info?.customer_phone_last4 ? `•••${info.customer_phone_last4}` : ''
  const busy = phase === 'countdown' || phase === 'starting' || running

  return (
    <div className="flex h-dvh overflow-hidden bg-stone-100 text-stone-800" data-testid="director">
      <aside className="flex w-[360px] shrink-0 flex-col border-r border-black/[0.07] bg-white">
        <header className="flex items-center gap-2 border-b border-black/[0.06] px-5 py-3.5">
          <Radio size={16} className="text-stone-500" />
          <h1 className="text-sm font-semibold tracking-tight">Call console</h1>
          {phase === 'running' && callMode === 'replay' && (
            <span className="ml-auto rounded-full bg-stone-100 px-2 py-0.5 text-[11px] font-medium text-stone-500">Replay</span>
          )}
        </header>

        <div className="space-y-3 border-b border-black/[0.06] px-5 py-4">
          <Segmented value={scenario} options={SCENARIOS} onChange={(v) => setScenario(v as ScenarioId)} disabled={busy} testid="scenario" />
          <Segmented
            value={mode}
            options={[{ id: 'live', label: 'Live' }, { id: 'replay', label: 'Replay' }]}
            onChange={(v) => setMode(v as Mode)}
            disabled={busy}
            testid="mode"
          />
          {mode === 'replay' && (
            <div className="flex gap-2">
              <select
                value={take}
                onChange={(e) => setTake(e.target.value)}
                disabled={busy}
                className="min-w-0 flex-1 rounded-lg border border-black/10 bg-white px-2.5 py-1.5 text-sm"
                data-testid="take-picker"
              >
                {scenarioTakes.length === 0 && <option value="">No passing runs yet</option>}
                {scenarioTakes.map((t) => (
                  <option key={t.run} value={t.run}>
                    {takeLabel(t)}
                  </option>
                ))}
              </select>
              <button
                onClick={starTake}
                disabled={!take || selectedStarred}
                title={selectedStarred ? 'Starred: in backend/takes/' : 'Star: copy this run into backend/takes/'}
                className={`rounded-lg border border-black/10 px-2.5 hover:bg-stone-50 disabled:cursor-default ${selectedStarred ? 'text-amber-500' : 'text-stone-400'}`}
                data-testid="star-take"
              >
                <Star size={15} fill={selectedStarred ? 'currentColor' : 'none'} />
              </button>
            </div>
          )}
          <button
            onClick={start}
            disabled={busy || (mode === 'replay' && !take)}
            className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-stone-900 text-[15px] font-semibold text-white transition-colors duration-150 hover:bg-stone-800 disabled:bg-stone-300"
            data-testid="start"
          >
            {phase === 'starting' ? <LoaderCircle size={18} className="animate-spin" /> : <Play size={16} fill="currentColor" />}
            {phase === 'countdown' ? `Starting in ${count}…` : running ? 'Call in progress' : 'Start'}
          </button>
          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>

        <div className="border-b border-black/[0.06] px-5 py-4" data-testid="call-card">
          {info ? (
            <>
              <div className="flex items-baseline justify-between">
                <span className="text-[15px] font-semibold tracking-tight text-stone-900">{info.brand}</span>
                <span className="font-mono text-sm text-stone-600 tabular-nums" data-testid="call-timer">
                  {callTime ?? (lastStatus === 'dialing' ? 'Dialing…' : lastStatus === 'ringing' ? 'Ringing…' : '')}
                </span>
              </div>
              <div className="mt-0.5 text-[13px] text-stone-500" data-testid="call-direction">
                {direction === 'inbound' ? `Inbound ← ${who}, ${phone}` : `Outbound → ${who}, ${phone}`}
              </div>
            </>
          ) : (
            <p className="text-[13px] text-stone-400">No call yet. Pick a scenario and press Start.</p>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
          <Timeline events={events} direction={direction} waitingFor={waitingForClick ? 'confirmed' : null} />
        </div>

        <div className="space-y-2 border-t border-black/[0.06] px-5 py-3">
          <NowSpeaking speaking={speaking} engine={engine} />
          {waitingForClick && (
            <div className="flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[13px] text-amber-800 animate-fade-in" data-testid="waiting-confirm">
              <span className="relative flex h-2 w-2">
                <span className="absolute inset-0 rounded-full bg-amber-500 animate-live-ping" />
                <span className="relative h-2 w-2 rounded-full bg-amber-500" />
              </span>
              Waiting for confirm click
            </div>
          )}
        </div>

        <footer className="border-t border-black/[0.06] px-5 py-2.5 text-[11px] text-stone-400" data-testid="honesty-footer">
          {(phase === 'idle' ? mode : callMode) === 'replay'
            ? 'Replay of a live run · pauses shortened · telephony simulated'
            : 'Telephony simulated · agent, tools, database and link are live'}
        </footer>
      </aside>

      <main className="relative min-w-0 flex-1 p-6">
        <CustomerScreen
          key={callId ?? 'none'}
          events={events}
          linkUrl={info?.link_url ?? null}
          agentName={info?.agent_name ?? null}
          customerFirstName={info?.customer_name?.split(' ')[0] ?? null}
          sentAt={sent ? (receivedAt[sent.seq] ?? null) : null}
          playingTurnId={speaking?.turnId ?? null}
        />
        {phase === 'countdown' && (
          <div className="absolute inset-0 flex items-center justify-center bg-stone-100/70 backdrop-blur-[2px]" data-testid="countdown">
            <span key={count} className="text-[120px] font-semibold text-stone-800 tabular-nums animate-fade-in">
              {count}
            </span>
          </div>
        )}
      </main>
    </div>
  )
}

function Segmented({
  value,
  options,
  onChange,
  disabled,
  testid,
}: {
  value: string
  options: { id: string; label: string }[]
  onChange: (v: string) => void
  disabled?: boolean
  testid: string
}) {
  return (
    <div className="flex rounded-lg bg-stone-100 p-0.5" role="radiogroup" data-testid={testid}>
      {options.map((o) => (
        <button
          key={o.id}
          role="radio"
          aria-checked={value === o.id}
          disabled={disabled}
          onClick={() => onChange(o.id)}
          data-testid={`${testid}-${o.id}`}
          className={`flex-1 rounded-md px-2 py-1.5 text-[13px] transition-colors duration-150 ${
            value === o.id ? 'bg-white font-medium text-stone-900 shadow-[0_1px_1px_rgba(0,0,0,0.06)]' : 'text-stone-500 hover:text-stone-800'
          } disabled:cursor-not-allowed`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
