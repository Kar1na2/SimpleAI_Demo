import { Lock } from 'lucide-react'
import { useEffect } from 'react'
import { mmss, timeOfDay } from '../../lib/link/format.ts'
import { useNow } from '../../lib/link/hooks.ts'
import type { Meta } from '../../lib/link/reducer.ts'

export function BrandMark({ name, size = 'md' }: { name: string | null; size?: 'md' | 'lg' }) {
  const initials = (name ?? '·')
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
  const box = size === 'lg' ? 'h-10 w-10 text-sm' : 'h-8 w-8 text-[11px]'
  return (
    <span className={`inline-flex ${box} shrink-0 items-center justify-center rounded-lg bg-accent font-semibold tracking-wide text-white`}>
      {initials}
    </span>
  )
}

export function LiveDot({ on }: { on: boolean }) {
  return (
    <span className="relative inline-flex h-2 w-2" aria-hidden>
      {on && <span className="absolute inset-0 rounded-full bg-emerald-500 animate-live-ping" />}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${on ? 'bg-emerald-500' : 'bg-stone-300'}`} />
    </span>
  )
}

type Props = {
  meta: Meta
  callStatus: string
  connected: boolean
  compact: boolean
  onExpire: () => void
}

export function Header({ meta, callStatus, connected, compact, onExpire }: Props) {
  const now = useNow(1000)
  const remaining = Date.parse(meta.expires_at) - now
  const expired = remaining <= 0
  useEffect(() => {
    if (expired) onExpire()
  }, [expired, onExpire])

  const agent = meta.agent_name ?? 'your agent'
  const ended = callStatus === 'ended'
  const live = connected && callStatus === 'connected'
  const status = (
    <span className="inline-flex items-center gap-2 text-sm text-stone-600" data-testid="call-status">
      <LiveDot on={live} />
      {ended ? 'Call ended' : `Live call with ${agent}`}
    </span>
  )
  const expiry = (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border border-black/[0.07] bg-white whitespace-nowrap text-stone-700 ${compact ? 'px-2.5 py-0.5 text-xs' : 'px-3 py-1 text-[13px]'}`}
    >
      <Lock size={compact ? 11 : 13} strokeWidth={2.2} className="text-accent" aria-hidden />
      {compact ? 'Private · ' : 'Private link · expires in '}
      <span className="font-medium tabular-nums" data-testid="expires-in" title="Link expires in">
        {mmss(remaining)}
      </span>
    </span>
  )

  if (compact) {
    return (
      <header className="border-b border-black/[0.06] bg-surface">
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <BrandMark name={meta.brand} />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold tracking-tight text-stone-900">{meta.brand}</div>
              <div className="-mt-0.5 text-xs">{status}</div>
            </div>
          </div>
          {expiry}
        </div>
      </header>
    )
  }

  return (
    <header className="shrink-0 border-b border-black/[0.06] bg-surface/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1360px] items-center justify-between px-8 py-4">
        <div className="flex items-center gap-3">
          <BrandMark name={meta.brand} />
          <div>
            <div className="text-[15px] font-semibold tracking-tight text-stone-900">{meta.brand}</div>
            {status}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          {expiry}
          <span className="text-xs text-stone-500" data-testid="link-created">
            Link created when your call connected at {timeOfDay(meta.link_created_at)}
          </span>
        </div>
      </div>
    </header>
  )
}
