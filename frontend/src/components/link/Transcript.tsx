import { useEffect, useLayoutEffect, useRef } from 'react'
import { useReached } from '../../lib/link/hooks.ts'
import type { LinkState, Turn } from '../../lib/link/reducer.ts'
import { wordDiff } from '../../lib/link/wordDiff.ts'

function CorrectedText({ heard, said }: { heard: string; said: string }) {
  return (
    <>
      {wordDiff(heard, said).map((part, i) => (
        <span key={i}>
          {i > 0 && ' '}
          {part.op === 'same' ? (
            part.text
          ) : part.op === 'del' ? (
            <del className="rounded-sm bg-red-50 px-0.5 text-red-600 decoration-red-500 decoration-2" data-testid="heard-wrong">
              {part.text}
            </del>
          ) : (
            <ins className="rounded-sm bg-emerald-50 px-0.5 font-medium text-emerald-700 no-underline" data-testid="heard-right">
              {part.text}
            </ins>
          )}
        </span>
      ))}
    </>
  )
}

function Bubble({ turn, agentName, fresh }: { turn: Turn; agentName: string; fresh: boolean }) {
  const agent = turn.speaker === 'agent'
  return (
    <div
      className={`flex gap-2.5 ${agent ? '' : 'flex-row-reverse'} ${fresh ? 'animate-slide-in' : ''}`}
      data-testid={`turn-${turn.speaker}`}
    >
      {agent && (
        <span className="mt-5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-white">
          {agentName[0]}
        </span>
      )}
      <div className={`flex max-w-[82%] flex-col ${agent ? 'items-start' : 'items-end'}`}>
        <div className="mb-1 flex items-center gap-2 px-1 text-[11px] font-medium text-stone-400">
          {agent ? agentName : 'You'}
          {turn.correction && (
            <span className="rounded-full bg-emerald-50 px-1.5 py-px text-[10px] font-medium text-emerald-700 ring-1 ring-emerald-200" data-testid="corrected-tag">
              corrected
            </span>
          )}
        </div>
        <div
          className={`rounded-2xl px-3.5 py-2.5 text-[14px] leading-relaxed ${
            agent
              ? 'rounded-tl-md border border-black/[0.07] bg-white text-stone-800'
              : 'rounded-tr-md bg-accent-soft text-stone-900 ring-1 ring-accent-line'
          }`}
        >
          {turn.correction ? <CorrectedText heard={turn.textHeard} said={turn.correction.correctedText} /> : turn.textHeard}
        </div>
      </div>
    </div>
  )
}

function Typing({ agentName }: { agentName: string }) {
  return (
    <div className="flex gap-2.5 animate-fade-in" data-testid="typing">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-white">
        {agentName[0]}
      </span>
      <div className="flex items-center gap-1 rounded-2xl rounded-tl-md border border-black/[0.07] bg-white px-3.5 py-3" aria-label={`${agentName} is responding`}>
        {[0, 1, 2].map((i) => (
          <span key={i} className="h-1.5 w-1.5 rounded-full bg-stone-400 animate-typing-dot" style={{ animationDelay: `${i * 150}ms` }} />
        ))}
      </div>
    </div>
  )
}

export function Transcript({ state, agentName, embedded = false }: { state: LinkState; agentName: string; embedded?: boolean }) {
  const scroller = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const ended = state.callStatus === 'ended'
  const typing = useReached(ended ? null : state.agentReplyDueAt)

  const onScroll = () => {
    const el = scroller.current
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 56
  }
  useLayoutEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTo({ top: el.scrollHeight, behavior: state.turns.length > 1 ? 'smooth' : 'auto' })
  }, [state.turns.length, typing, state.turns])
  useEffect(() => {
    stick.current = true
  }, [])

  return (
    <section className={`flex min-h-0 flex-col ${embedded ? '' : 'h-full'}`} data-testid="transcript">
      {!embedded && (
        <div className="border-b border-black/[0.06] px-5 py-3.5">
          <h2 className="text-sm font-semibold text-stone-900">What {agentName} heard</h2>
          <p className="text-xs text-stone-500">Live transcript. Say so if anything's wrong.</p>
        </div>
      )}
      <div ref={scroller} onScroll={onScroll} className={`min-h-0 flex-1 space-y-4 overflow-y-auto ${embedded ? 'py-2' : 'px-5 py-4'}`}>
        {embedded && <h2 className="text-lg font-semibold tracking-tight text-stone-900">What {agentName} heard</h2>}
        {state.turns.map((t) => (
          <Bubble key={t.seq} turn={t} agentName={agentName} fresh={t.seq > state.liveFrom} />
        ))}
        {typing && <Typing agentName={agentName} />}
        {ended && (
          <div className="flex items-center gap-3 py-2 text-xs text-stone-400">
            <span className="h-px flex-1 bg-black/[0.06]" /> Call ended <span className="h-px flex-1 bg-black/[0.06]" />
          </div>
        )}
      </div>
    </section>
  )
}
