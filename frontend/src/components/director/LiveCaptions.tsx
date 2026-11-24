// "Live call" captions for the operator, shown above the phone mockup until the customer opens the link.
// Data: the last 3 transcript.turn events. Shows text_said, what was actually spoken (the operator hears
// the real audio; a mishearing exists only in what the agent heard). The line whose clip is playing
// right now is highlighted.
import { Volume2 } from 'lucide-react'
import type { CallEvent } from '../../lib/useCallEvents.ts'

const VISIBLE_TURNS = 3

type Props = { events: CallEvent[]; playingTurnId: number | null }

export function LiveCaptions({ events, playingTurnId }: Props) {
  const turns = events.filter((e) => e.type === 'transcript.turn').slice(-VISIBLE_TURNS)
  const status = [...events].reverse().find((e) => e.type === 'call.status')?.payload.status
  const onCall = status === 'connected'

  return (
    <section className="w-[680px] rounded-2xl border border-black/[0.07] bg-white" data-testid="captions">
      <header className="flex items-center justify-between border-b border-black/[0.06] px-5 py-3">
        <div className="flex items-center gap-2 text-[15px] font-semibold tracking-tight text-stone-900">
          <span className={`h-2 w-2 rounded-full ${onCall ? 'bg-red-500' : 'bg-stone-300'}`} aria-hidden />
          Live call
        </div>
        <span className="text-[14px] text-stone-400">Call audio · operator view</span>
      </header>
      {/* Fixed height, newest line at the bottom: the phone mockup below never shifts. */}
      {turns.length === 0 ? (
        <p className="flex h-[236px] items-center justify-center text-[15px] text-stone-400">
          {status === 'dialing' ? 'Dialing…' : status === 'ringing' ? 'Ringing…' : status ? 'Connected' : 'No call yet'}
        </p>
      ) : (
        <div className="flex h-[236px] flex-col justify-end gap-1 overflow-hidden p-3 [mask-image:linear-gradient(to_bottom,transparent,black_20px)]">
          {turns.map((e) => {
            const turnId = Number(e.payload.turn_id)
            const playing = turnId === playingTurnId
            return (
              <div
                key={turnId}
                className={`grid shrink-0 grid-cols-[84px_1fr] gap-3 rounded-lg px-3 py-2 transition-colors duration-200 animate-slide-in ${playing ? 'bg-stone-100' : ''}`}
                data-testid="caption-line"
                data-turn-id={turnId}
                data-playing={playing}
              >
                <span className={`flex items-center gap-1.5 self-start text-[15px] leading-6 font-semibold ${playing ? 'text-stone-900' : 'text-stone-500'}`}>
                  {String(e.payload.name ?? '').split(' ')[0]}
                  <Volume2 size={14} className={`shrink-0 transition-opacity duration-200 ${playing ? 'opacity-100' : 'opacity-0'}`} aria-hidden />
                </span>
                <p className={`text-[16px] leading-6 transition-colors duration-200 ${playing ? 'text-stone-900' : 'text-stone-500'}`} data-testid="caption-text">
                  {String(e.payload.text_said ?? '')}
                </p>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
