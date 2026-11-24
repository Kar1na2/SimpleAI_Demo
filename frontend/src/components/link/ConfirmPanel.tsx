import { ArrowRight, Check, LoaderCircle } from 'lucide-react'
import { useState } from 'react'
import { clock, money, shortDate } from '../../lib/link/format.ts'
import { isCommitted, type LinkState } from '../../lib/link/reducer.ts'
import type { Reservation, ReservationChange } from '../../lib/link/types.ts'

function fieldValue(field: ReservationChange['field'], v: number | string) {
  if (field === 'party_size') return `${v} people`
  if (field === 'date') return shortDate(String(v))
  return clock(String(v))
}
const FIELD_LABEL = { party_size: 'Party', date: 'Date', time: 'Time' } as const

function ReservationReview({ r }: { r: Reservation }) {
  const changed = new Map(r.changes.map((c) => [c.field, c]))
  return (
    <dl className="divide-y divide-black/[0.06] rounded-xl border border-black/[0.07] bg-white">
      {(['party_size', 'date', 'time'] as const).map((f) => {
        const c = changed.get(f)
        return (
          <div key={f} className="flex items-center justify-between px-5 py-3.5" data-testid={`review-${f}`}>
            <dt className="text-sm text-stone-500">{FIELD_LABEL[f]}</dt>
            <dd className="flex items-center gap-2.5 text-[15px] tabular-nums">
              {c ? (
                <>
                  <span className="text-stone-400 line-through">{fieldValue(f, c.from)}</span>
                  <ArrowRight size={14} className="text-stone-400" />
                  <span className="font-semibold text-stone-900">{fieldValue(f, c.to)}</span>
                </>
              ) : (
                <span className="text-stone-700">{fieldValue(f, r[f])}</span>
              )}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

function OrderReview({ state }: { state: LinkState }) {
  const cart = state.cart!
  return (
    <div className="rounded-xl border border-black/[0.07] bg-white">
      <ul className="divide-y divide-black/[0.06]">
        {cart.items.map((i) => (
          <li key={i.sku} className="flex items-center gap-4 px-5 py-3">
            <img src={i.image_path} alt="" className="h-10 w-12 rounded-md border border-black/[0.06] object-cover" />
            <span className="flex-1 text-sm text-stone-800">
              {i.name} <span className="text-stone-400">× {i.qty}</span>
            </span>
            <span className="text-sm font-medium tabular-nums">{money(i.line_total_cents)}</span>
          </li>
        ))}
      </ul>
      <dl className="space-y-1.5 border-t border-black/[0.06] px-5 py-3.5 text-sm">
        <div className="flex justify-between text-stone-600">
          <dt>Subtotal</dt>
          <dd className="tabular-nums">{money(cart.subtotal_cents)}</dd>
        </div>
        <div className="flex justify-between text-stone-600">
          <dt>Shipping</dt>
          <dd className="tabular-nums">{cart.free_shipping ? 'Free' : money(cart.shipping_cents)}</dd>
        </div>
        <div className="flex justify-between pt-1 text-base font-semibold text-stone-900">
          <dt>Total</dt>
          <dd className="tabular-nums">{money(cart.total_cents)}</dd>
        </div>
      </dl>
    </div>
  )
}

export function ConfirmPanel({ state, token, agentName }: { state: LinkState; token: string; agentName: string }) {
  const [sending, setSending] = useState(false)
  const [clicked, setClicked] = useState(false)
  const [quiet, setQuiet] = useState<string | null>(null)
  const isOrder = state.cart != null

  if (isCommitted(state)) {
    const r = state.reservation
    return (
      <div className="flex flex-col items-center py-10 text-center animate-fade-in" data-testid="confirm-done">
        <span className="flex h-14 w-14 items-center justify-center rounded-full bg-accent text-white">
          <Check size={28} strokeWidth={2.5} />
        </span>
        <h2 className="mt-5 text-2xl font-semibold tracking-tight text-stone-900">
          {isOrder ? 'Order placed ✓' : 'Reservation confirmed ✓'}
        </h2>
        <p className="mt-2 text-sm text-stone-500 tabular-nums">
          {isOrder && state.cart?.order
            ? `Order #${state.cart.order.id} · ${money(state.cart.order.total_cents)}`
            : r
              ? `Party of ${r.party_size} · ${shortDate(r.date)} · ${clock(r.time)}`
              : null}
        </p>
      </div>
    )
  }

  if (clicked || state.confirmReceivedSeq != null) {
    return (
      <div className="flex flex-col items-center py-12 text-center animate-fade-in" data-testid="confirm-finalizing">
        <LoaderCircle size={28} className="animate-spin text-accent" />
        <h2 className="mt-4 text-lg font-semibold text-stone-900">Confirmed, {agentName} is finalizing…</h2>
      </div>
    )
  }

  const confirm = async () => {
    setSending(true)
    setQuiet(null)
    try {
      const r = await fetch(`/api/s/${encodeURIComponent(token)}/confirm`, { method: 'POST' })
      if (r.ok) setClicked(true)
      else if (r.status === 409) {
        const detail = (await r.json().catch(() => ({}))).detail
        setQuiet(detail === 'Already confirmed' ? 'This was already confirmed.' : 'There’s nothing to confirm right now.')
      } else setQuiet(`That didn’t go through. You can tell ${agentName} instead.`)
    } catch {
      setQuiet(`That didn’t go through. You can tell ${agentName} instead.`)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="mx-auto max-w-xl animate-fade-in" data-testid="confirm-review">
      <h2 className="text-lg font-semibold tracking-tight text-stone-900">
        {isOrder ? 'Review your order' : 'Review your change'}
      </h2>
      <p className="mt-1 text-sm text-stone-500">This is exactly what {agentName} will {isOrder ? 'place' : 'book'}.</p>
      <div className="mt-4">{isOrder ? <OrderReview state={state} /> : state.reservation && <ReservationReview r={state.reservation} />}</div>
      <button
        type="button"
        onClick={confirm}
        disabled={sending}
        data-testid="confirm-button"
        className="mt-5 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-accent text-base font-semibold text-white transition-[filter,transform] duration-150 hover:brightness-110 active:scale-[0.99] disabled:opacity-70"
      >
        {sending && <LoaderCircle size={18} className="animate-spin" />}
        Confirm
      </button>
      <p className="mt-3 text-center text-sm text-stone-500">Something wrong? Just tell {agentName}. Nothing is final until you confirm.</p>
      {quiet && (
        <p className="mt-2 text-center text-xs text-stone-400" data-testid="confirm-quiet">
          {quiet}
        </p>
      )}
    </div>
  )
}
