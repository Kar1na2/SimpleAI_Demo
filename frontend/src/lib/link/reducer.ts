// All link-page state comes from one reducer over (1) the GET /api/s/{token} snapshot and
// (2) the call events from /ws/s/{token}. Nothing here is invented: every value is a field
// of the snapshot or of an event payload.
//
// The page streams the full history (since=0) so the transcript, notes and actions are
// complete, and resumes from the last seq after a reconnect. Events with seq <= snapshot.seq
// are history: they fill the lists but don't animate, and older state.* events don't
// overwrite the (newer) snapshot.

import type { Cart, CallEvent, Reservation, Slot, Snapshot } from './types.ts'

export type Turn = {
  seq: number
  turnId: number
  speaker: 'agent' | 'customer'
  name: string
  textHeard: string
  at: number
  durationMs: number
  correction?: { correctedText: string; seq: number }
}

export type Note = { seq: number; text: string; at: number }

export type ActionKind = 'tool' | 'link' | 'confirm'
export type Action = { seq: number; kind: ActionKind; icon: string; title: string; ok: boolean; at: number }

export type Availability = {
  seq: number
  date: string
  slots: { time: string; status: 'open' | 'full' }[]
  offered: string[] // the two nearest open times the agent was given to offer
}

export type Meta = Omit<Snapshot, 'cart' | 'reservation' | 'slots'>

export type LinkState = {
  meta: Meta | null
  liveFrom: number // events with seq > liveFrom happened while this page was open
  lastSeq: number
  callStatus: string
  cart: Cart | null
  reservation: Reservation | null
  slots: Slot[]
  availability: Availability | null
  turns: Turn[]
  notes: Note[]
  actions: Action[]
  confirmRequestedSeq: number | null
  confirmReceivedSeq: number | null
  firstStateSeq: number | null
  agentReplyDueAt: number | null // when the customer finished speaking; cleared when the agent responds
  cartChanges: Record<string, { seq: number; kind: 'added' | 'qty' }>
  reservationChanges: Record<string, number> // field -> seq of the event that changed it
}

export type LinkAction =
  | { type: 'snapshot'; snapshot: Snapshot }
  | { type: 'event'; event: CallEvent }
  | { type: 'reset' }

export const initialLinkState: LinkState = {
  meta: null,
  liveFrom: 0,
  lastSeq: 0,
  callStatus: 'connected',
  cart: null,
  reservation: null,
  slots: [],
  availability: null,
  turns: [],
  notes: [],
  actions: [],
  confirmRequestedSeq: null,
  confirmReceivedSeq: null,
  firstStateSeq: null,
  agentReplyDueAt: null,
  cartChanges: {},
  reservationChanges: {},
}

const last4 = (masked: string) => (masked.match(/(\d{4})\D*$/) ?? [])[1] ?? ''

function linkAction(e: CallEvent): { icon: string; title: string } | null {
  switch (e.type) {
    case 'link.minted':
      return { icon: 'link.minted', title: 'Secure link created (call connected)' }
    case 'link.sent':
      return { icon: 'link.sent', title: `Link texted to •••${last4(String(e.payload.to ?? ''))}` }
    case 'link.opened':
      return { icon: 'link.opened', title: 'You opened this link' }
    case 'link.revoked':
      return { icon: 'link.revoked', title: 'Link closed when the call ended' }
    default:
      return null
  }
}

function diffCart(prev: Cart | null, next: Cart, seq: number, changes: LinkState['cartChanges']) {
  const out = { ...changes }
  const before = new Map((prev?.items ?? []).map((i) => [i.sku, i.qty]))
  for (const item of next.items) {
    const old = before.get(item.sku)
    if (old === undefined) out[item.sku] = { seq, kind: 'added' }
    else if (old !== item.qty) out[item.sku] = { seq, kind: 'qty' }
  }
  return out
}

function diffReservation(prev: Reservation | null, next: Reservation, seq: number, changes: Record<string, number>) {
  if (!prev) return changes
  const out = { ...changes }
  for (const f of ['party_size', 'date', 'time', 'status'] as const) if (prev[f] !== next[f]) out[f] = seq
  return out
}

export function linkReducer(state: LinkState, action: LinkAction): LinkState {
  if (action.type === 'reset') return initialLinkState
  if (action.type === 'snapshot') {
    const { cart, reservation, slots, ...meta } = action.snapshot
    return {
      ...state,
      meta,
      liveFrom: Math.max(state.liveFrom, action.snapshot.seq),
      callStatus: action.snapshot.status,
      cart,
      reservation,
      slots: slots ?? [],
    }
  }

  const e = action.event
  if (e.seq <= state.lastSeq) return state // already applied (reconnect overlap)
  const p = e.payload
  const at = Date.parse(e.created_at)
  const live = e.seq > state.liveFrom
  const s: LinkState = { ...state, lastSeq: e.seq }

  switch (e.type) {
    case 'call.status':
      s.callStatus = String(p.status)
      if (p.status === 'ended') s.agentReplyDueAt = null
      break

    case 'transcript.turn': {
      const turn: Turn = {
        seq: e.seq,
        turnId: Number(p.turn_id),
        speaker: p.speaker === 'agent' ? 'agent' : 'customer',
        name: String(p.name ?? ''),
        textHeard: String(p.text_heard ?? p.text_said ?? ''),
        at,
        durationMs: Number(p.duration_ms ?? 0),
      }
      s.turns = [...state.turns, turn]
      s.agentReplyDueAt = turn.speaker === 'customer' ? at + turn.durationMs : null
      break
    }

    case 'transcript.correction':
      s.turns = state.turns.map((t) =>
        t.turnId === Number(p.turn_id) ? { ...t, correction: { correctedText: String(p.corrected_text), seq: e.seq } } : t,
      )
      break

    case 'note.added':
      s.notes = [...state.notes, { seq: e.seq, text: String(p.text), at }]
      break

    case 'agent.tool_call':
      // A successful send_link is already shown by its link.sent entry ("Link texted to •••0142").
      if (!(p.name === 'send_link' && p.ok))
        s.actions = [
          ...state.actions,
          { seq: e.seq, kind: 'tool', icon: String(p.name), title: String(p.summary), ok: Boolean(p.ok), at },
        ]
      s.agentReplyDueAt = null
      if (p.name === 'check_availability' && p.ok && p.data?.slots?.length) {
        s.availability = {
          seq: e.seq,
          date: String(p.data.date),
          slots: p.data.slots,
          offered: p.data.your_time && p.data.your_time.status !== 'open' ? (p.data.nearest_open_to_your_time ?? []) : [],
        }
      }
      break

    case 'state.cart':
      s.firstStateSeq ??= e.seq
      if (live) {
        s.cartChanges = diffCart(state.cart, p as Cart, e.seq, state.cartChanges)
        s.cart = p as Cart
      }
      break

    case 'state.reservation':
      s.firstStateSeq ??= e.seq
      if (live) {
        s.reservationChanges = diffReservation(state.reservation, p as Reservation, e.seq, state.reservationChanges)
        s.reservation = p as Reservation
      }
      break

    case 'confirm.requested':
      s.confirmRequestedSeq = e.seq
      break

    case 'confirm.received':
      s.confirmReceivedSeq = e.seq
      s.actions = [
        ...state.actions,
        { seq: e.seq, kind: 'confirm', icon: 'confirm.received', title: 'You confirmed on this link', ok: true, at },
      ]
      break

    default: {
      const link = linkAction(e)
      if (link) s.actions = [...state.actions, { seq: e.seq, kind: 'link', ok: true, at, ...link }]
    }
  }
  return s
}

/** Has the agent committed? (order placed / reservation confirmed), straight from state. */
export function isCommitted(state: LinkState): boolean {
  return state.cart?.order?.status === 'placed' || state.reservation?.status === 'confirmed'
}
