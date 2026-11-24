// The customer's side. Before the link opens: the operator's "Live call" captions above a phone
// mockup with the (simulated) SMS. Then the real link page in an iframe rendered at 1100x800
// (desktop layout) and scaled to fit; the pre-link panel fades out over it. The bubble shows
// link.sent's sms_text, which never contains the host, port or full token; the real URL is only
// the iframe's src.
import { MessageSquareText, Smartphone } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CallEvent } from '../../lib/useCallEvents.ts'
import { LiveCaptions } from './LiveCaptions.tsx'

const W = 1100
const H = 800
const OPEN_DELAY_MS = 1000
const FADE_MS = 300

type Props = {
  events: CallEvent[]
  linkUrl: string | null
  agentName: string | null
  customerFirstName: string | null
  sentAt: number | null // local time link.sent arrived
  playingTurnId: number | null // transcript turn whose audio is playing now
}

export function CustomerScreen({ events, linkUrl, agentName, customerFirstName, sentAt, playingTurnId }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0.9)
  const [src, setSrc] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const sent = events.find((e) => e.type === 'link.sent')
  const opened = events.some((e) => e.type === 'link.opened')
  const connected = events.some((e) => e.type === 'call.status' && e.payload.status === 'connected')

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const fit = () => setScale(Math.min(el.clientWidth / W, el.clientHeight / H, 1))
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ~1 s after the text arrives, the customer opens the link (same origin as this page).
  useEffect(() => {
    if (!sentAt || !linkUrl || src) return
    const path = new URL(linkUrl).pathname
    const id = window.setTimeout(() => setSrc(path), Math.max(0, sentAt + OPEN_DELAY_MS - Date.now()))
    return () => window.clearTimeout(id)
  }, [sentAt, linkUrl, src])

  const showPage = Boolean(src && loaded && opened)
  // The pre-link panel fades out (FADE_MS) while the iframe fades in, then unmounts.
  const [panelGone, setPanelGone] = useState(false)
  useEffect(() => {
    if (!showPage) return
    const id = window.setTimeout(() => setPanelGone(true), FADE_MS + 50)
    return () => window.clearTimeout(id)
  }, [showPage])
  const who = customerFirstName ? `${customerFirstName}'s` : "Customer's"

  return (
    <div ref={box} className="relative flex h-full w-full items-center justify-center" data-testid="customer-screen">
      <div
        className="relative overflow-hidden rounded-xl border border-black/10 bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
        style={{ width: W * scale, height: H * scale }}
      >
        {src && (
          <iframe
            title="Customer link page"
            src={src}
            onLoad={() => setLoaded(true)}
            className={`absolute top-0 left-0 origin-top-left border-0 transition-opacity duration-250 ${showPage ? 'opacity-100' : 'opacity-0'}`}
            style={{ width: W, height: H, transform: `scale(${scale})` }}
            data-testid="link-iframe"
          />
        )}
        {!panelGone && (
          <div
            className={`absolute inset-0 flex flex-col items-center justify-center gap-8 bg-stone-50 transition-opacity ease-out ${showPage ? 'pointer-events-none opacity-0' : 'opacity-100'}`}
            style={{ transitionDuration: `${FADE_MS}ms` }}
            data-testid="waiting-state"
          >
            <LiveCaptions events={events} playingTurnId={playingTurnId} />
            {/* Fixed height so the SMS arriving never moves the captions card. */}
            <div className="flex h-[236px] flex-col items-center gap-3">
              <div className="flex items-center gap-2 text-sm text-stone-500">
                <Smartphone size={16} />
                {who} phone
              </div>
              <div className="w-[380px] rounded-2xl border border-black/[0.07] bg-white p-4">
                <div className="mb-3 flex items-center gap-2 border-b border-black/[0.06] pb-3 text-xs text-stone-400">
                  <MessageSquareText size={14} /> Messages
                </div>
                {sent ? (
                  <div className="animate-slide-in" data-testid="sms-bubble">
                    {sent.payload.from ? (
                      <div className="mb-1 pl-1 text-[11px] font-medium text-stone-500">{String(sent.payload.from)}</div>
                    ) : null}
                    <div className="max-w-[300px] rounded-2xl rounded-bl-md bg-stone-100 px-3.5 py-2.5 text-[14px] leading-snug text-stone-800">
                      <SmsText text={String(sent.payload.sms_text ?? '')} />
                    </div>
                    <div className="mt-1.5 pl-1 text-[11px] text-stone-400">Text message · {String(sent.payload.to ?? '')}</div>
                  </div>
                ) : (
                  <p className="py-4 text-center text-sm text-stone-400">
                    {connected ? `Waiting for ${agentName ?? 'the agent'} to text the link…` : 'No messages yet'}
                  </p>
                )}
              </div>
              {sent && <p className="text-xs text-stone-400">Opening the link…</p>}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/** The SMS text, with a trailing "(expires in 30 min)" kept on one line. */
function SmsText({ text }: { text: string }) {
  const i = text.lastIndexOf(' (')
  if (i < 0) return text
  return (
    <>
      {text.slice(0, i)} <span className="whitespace-nowrap">{text.slice(i + 1)}</span>
    </>
  )
}
