import {
  CalendarCheck2,
  CalendarClock,
  CalendarCog,
  CalendarSearch,
  CircleCheck,
  ClipboardCheck,
  Eye,
  ListChecks,
  MessageSquareText,
  NotebookPen,
  PackageCheck,
  PencilLine,
  PhoneOff,
  Search,
  Send,
  ShieldCheck,
  ShoppingCart,
  StickyNote,
  Trash2,
  Unlink,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { timeOfDay } from '../../lib/link/format.ts'
import type { Action, Note } from '../../lib/link/reducer.ts'
import { EmptyState } from './CartView.tsx'

const ICONS: Record<string, LucideIcon> = {
  lookup_product: Search,
  add_to_cart: ShoppingCart,
  update_cart_item: PencilLine,
  remove_cart_item: Trash2,
  get_reservation: CalendarSearch,
  check_availability: CalendarClock,
  update_reservation: CalendarCog,
  add_note: StickyNote,
  send_link: MessageSquareText,
  request_confirmation: ClipboardCheck,
  place_order: PackageCheck,
  confirm_reservation: CalendarCheck2,
  end_call: PhoneOff,
  'link.minted': ShieldCheck,
  'link.sent': Send,
  'link.opened': Eye,
  'link.revoked': Unlink,
  'confirm.received': CircleCheck,
}

export function NotesTab({ notes, liveFrom, agentName }: { notes: Note[]; liveFrom: number; agentName: string }) {
  if (notes.length === 0) return <EmptyState icon={NotebookPen} text={`Notes ${agentName} takes during the call show up here.`} />
  return (
    <div data-testid="notes">
      <h2 className="text-lg font-semibold tracking-tight text-stone-900">What {agentName} is noting</h2>
      <ul className="mt-3 space-y-2">
        {[...notes].reverse().map((n) => {
          const fresh = n.seq > liveFrom
          return (
            <li
              key={n.seq}
              className={`relative overflow-hidden rounded-lg border border-black/[0.06] bg-white px-4 py-3 ${fresh ? 'animate-slide-in' : ''}`}
            >
              {fresh && <span className="pointer-events-none absolute inset-0 animate-flash-accent" aria-hidden />}
              <div className="relative flex items-start justify-between gap-4">
                <p className="text-sm text-stone-800">{n.text}</p>
                <time className="shrink-0 text-xs text-stone-400 tabular-nums">{timeOfDay(n.at)}</time>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function ActionsTab({ actions, liveFrom, agentName }: { actions: Action[]; liveFrom: number; agentName: string }) {
  if (actions.length === 0) return <EmptyState icon={ListChecks} text={`Everything ${agentName} does on your behalf shows up here.`} />
  return (
    <div data-testid="actions">
      <h2 className="text-lg font-semibold tracking-tight text-stone-900">What {agentName} did</h2>
      <ol className="relative mt-4">
        <span className="absolute top-2 bottom-2 left-[15px] w-px bg-black/[0.08]" aria-hidden />
        {[...actions].reverse().map((a) => {
          const Icon = ICONS[a.icon] ?? Wrench
          const tone = !a.ok
            ? 'bg-red-50 text-red-500 ring-red-100'
            : a.kind === 'tool'
              ? 'bg-accent-soft text-accent ring-accent-line'
              : 'bg-stone-100 text-stone-500 ring-black/[0.06]'
          return (
            <li
              key={a.seq}
              className={`relative flex gap-3 pb-4 last:pb-0 ${a.seq > liveFrom ? 'animate-slide-in' : ''}`}
              data-testid={`action-${a.icon}`}
              data-ok={a.ok}
            >
              <span className={`relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ring-1 ${tone}`}>
                <Icon size={15} strokeWidth={1.9} />
              </span>
              <div className="min-w-0 flex-1 pt-1.5">
                <div className="flex items-baseline justify-between gap-4">
                  <p className={`text-sm ${a.ok ? 'text-stone-800' : 'text-red-700/80'}`}>{a.title}</p>
                  <time className="shrink-0 text-xs text-stone-400 tabular-nums">{timeOfDay(a.at)}</time>
                </div>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
