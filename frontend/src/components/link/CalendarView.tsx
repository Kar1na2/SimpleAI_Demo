import { CalendarDays } from 'lucide-react'
import { clock, longDate, shortDate } from '../../lib/link/format.ts'
import type { Availability, LinkState } from '../../lib/link/reducer.ts'
import type { Reservation } from '../../lib/link/types.ts'
import { EmptyState } from './CartView.tsx'

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

/** Mon-first weeks covering the month of `isoDate`. */
function monthGrid(isoDate: string) {
  const [y, m] = isoDate.split('-').map(Number)
  const first = new Date(y, m - 1, 1)
  const start = new Date(first)
  start.setDate(1 - ((first.getDay() + 6) % 7))
  const weeks: { iso: string; day: number; inMonth: boolean }[][] = []
  const cur = new Date(start)
  while (weeks.length === 0 || cur.getMonth() === m - 1) {
    const week = []
    for (let i = 0; i < 7; i++) {
      week.push({ iso: iso(cur), day: cur.getDate(), inMonth: cur.getMonth() === m - 1 })
      cur.setDate(cur.getDate() + 1)
    }
    weeks.push(week)
  }
  return { label: first.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }), weeks }
}

const pendingFields = (r: Reservation) => new Set<string>(r.pending ? r.changes.map((c) => c.field) : [])

function DayChip({ r, dateIso }: { r: Reservation; dateIso: string }) {
  const pending = pendingFields(r)
  const slotPending = pending.has('date') || pending.has('time') // the date chip is about date and time only
  const isNew = r.date === dateIso
  const isOld = r.booked.date === dateIso && r.booked.date !== r.date
  if (isOld && slotPending)
    return (
      <span
        className="mt-1 inline-flex rounded-md border border-dashed border-stone-300 px-1.5 py-0.5 text-[11px] text-stone-400 line-through"
        data-testid={`cal-old-${dateIso}`}
      >
        {clock(r.booked.time)}
      </span>
    )
  if (!isNew) return null
  if (slotPending)
    return (
      <span
        className="mt-1 inline-flex flex-col items-start rounded-md border border-accent bg-white px-1.5 py-0.5 text-[11px] leading-tight font-medium text-accent animate-fade-in"
        data-testid={`cal-chip-${dateIso}`}
        data-state="pending"
      >
        {clock(r.time)}
        <span className="text-[10px] font-normal tracking-wide uppercase opacity-80">pending</span>
      </span>
    )
  return (
    <span
      className="mt-1 inline-flex rounded-md bg-accent px-1.5 py-0.5 text-[11px] font-medium text-white"
      data-testid={`cal-chip-${dateIso}`}
      data-state={r.status}
    >
      {clock(r.time)}
    </span>
  )
}

function Calendar({ r, slotDates, checking }: { r: Reservation; slotDates: Set<string>; checking: string | null }) {
  const { label, weeks } = monthGrid(r.date)
  return (
    <div data-testid="calendar">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold tracking-tight text-stone-900">{label}</h2>
        <div className="flex items-center gap-4 text-xs text-stone-500">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-accent" /> On the books
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm border border-accent bg-white" /> Pending change
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm border border-dashed border-accent" /> Checking
          </span>
        </div>
      </div>
      <div className="overflow-hidden rounded-xl border border-black/[0.07]">
        <div className="grid grid-cols-7 text-[10px] font-medium tracking-wider text-stone-500 uppercase">
          <div className="col-span-5" />
          <div className="col-span-2 bg-black/[0.045] px-2.5 pt-1.5 text-center" data-testid="weekend-label">
            Weekend
          </div>
        </div>
        <div className="grid grid-cols-7 border-b border-black/[0.07] text-[11px] font-medium tracking-wide text-stone-500 uppercase">
          {DOW.map((d, i) => (
            <div key={d} className={`px-2.5 py-1.5 ${i >= 5 ? 'bg-black/[0.045]' : ''}`}>
              {d}
            </div>
          ))}
        </div>
        {weeks.map((week, wi) => (
          <div key={wi} className="grid grid-cols-7 border-b border-black/[0.05] last:border-b-0">
            {week.map((d, i) => {
              const weekend = i >= 5
              const bookable = slotDates.has(d.iso)
              const isChecking = checking === d.iso
              const isReservation = d.inMonth && r.date === d.iso
              const bg = isReservation ? 'bg-accent-soft' : weekend ? 'bg-black/[0.045]' : 'bg-white'
              return (
                <div
                  key={d.iso}
                  data-testid={`cal-day-${d.iso}`}
                  className={`relative h-[62px] border-r border-black/[0.05] px-2.5 py-1.5 transition-colors duration-200 last:border-r-0 ${bg}`}
                >
                  {isReservation && <span className="pointer-events-none absolute inset-0 shadow-[inset_0_0_0_1.5px_var(--color-accent)]" aria-hidden />}
                  {isChecking && (
                    <span className="pointer-events-none absolute inset-[3px] rounded-lg border border-dashed border-accent animate-fade-in" aria-hidden />
                  )}
                  {isChecking && (
                    <span className="absolute bottom-1.5 left-2.5 text-[10px] font-medium tracking-wide text-accent uppercase" data-testid={`cal-checking-${d.iso}`}>
                      Checking
                    </span>
                  )}
                  <div
                    className={`relative text-xs tabular-nums ${isReservation ? 'font-semibold text-accent' : !d.inMonth ? 'text-stone-300' : bookable ? 'font-medium text-stone-800' : 'text-stone-400'}`}
                  >
                    {d.day}
                  </div>
                  {d.inMonth && <DayChip r={r} dateIso={d.iso} />}
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

function DetailsCard({ r, changes }: { r: Reservation; changes: Record<string, number> }) {
  const pending = pendingFields(r)
  const mark = (field: string) => (pending.has(field) ? 'underline decoration-accent decoration-dotted decoration-2 underline-offset-4' : '')
  const flash = (field: string) =>
    changes[field] ? (
      <span key={changes[field]} className="pointer-events-none absolute -inset-x-1.5 -inset-y-0.5 rounded-md animate-flash-amber" aria-hidden />
    ) : null
  const status =
    r.status === 'confirmed' ? 'Confirmed' : r.pending ? 'Pending your confirmation' : 'On the books'
  return (
    <div className="mt-4 flex items-center justify-between rounded-xl border border-black/[0.07] bg-white px-5 py-4" data-testid="reservation-details">
      <div className="text-[17px] font-semibold tracking-tight text-stone-900">
        <span className="relative" data-testid="detail-party">
          {flash('party_size')}
          <span className={`relative ${mark('party_size')}`} data-pending={pending.has('party_size') || undefined}>Party of {r.party_size}</span>
        </span>
        <span className="mx-2 text-stone-300">·</span>
        <span className="relative" data-testid="detail-date">
          {flash('date')}
          <span className={`relative ${mark('date')}`} data-pending={pending.has('date') || undefined}>{shortDate(r.date)}</span>
        </span>
        <span className="mx-2 text-stone-300">·</span>
        <span className="relative" data-testid="detail-time">
          {flash('time')}
          <span className={`relative ${mark('time')}`} data-pending={pending.has('time') || undefined}>{clock(r.time)}</span>
        </span>
      </div>
      <span
        className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
          r.status === 'confirmed'
            ? 'bg-accent text-white'
            : r.pending
              ? 'border border-accent text-accent'
              : 'bg-black/[0.05] text-stone-600'
        }`}
      >
        {status}
      </span>
    </div>
  )
}

function SlotChips({ a, r }: { a: Availability; r: Reservation }) {
  return (
    <div className="mt-4 animate-fade-in" data-testid={`slot-chips-${a.date}`}>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-stone-900">Times on {longDate(a.date)}</h3>
        <div className="flex items-center gap-3 text-[11px] text-stone-500">
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-emerald-500" /> Open
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-stone-300" /> Full
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full ring-2 ring-accent" /> Offered
          </span>
        </div>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(68px,1fr))] gap-1.5">
        {a.slots.map((s) => {
          const chosen = r.date === a.date && r.time === s.time
          const offered = a.offered.includes(s.time)
          const base = 'rounded-lg px-1.5 py-1.5 text-center text-[13px] whitespace-nowrap tabular-nums transition-colors duration-200'
          const look = chosen
            ? 'bg-accent font-medium text-white'
            : s.status === 'full'
              ? 'bg-stone-100 text-stone-400 line-through'
              : offered
                ? 'bg-emerald-50 font-medium text-emerald-800 ring-2 ring-accent'
                : 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200'
          return (
            <span key={s.time} className={`${base} ${look}`} data-testid={`slot-${s.time}`} data-status={s.status} data-offered={offered || undefined}>
              {clock(s.time)}
            </span>
          )
        })}
      </div>
    </div>
  )
}

export function CalendarView({ state }: { state: LinkState }) {
  const r = state.reservation
  if (!r) return <EmptyState icon={CalendarDays} text="Your reservation will appear here as you talk." />
  const slotDates = new Set(state.slots.map((s) => s.date))
  const a = state.availability
  return (
    <div>
      <Calendar r={r} slotDates={slotDates} checking={a && a.date !== r.date ? a.date : null} />
      <DetailsCard r={r} changes={state.reservationChanges} />
      {a && <SlotChips a={a} r={r} />}
    </div>
  )
}
