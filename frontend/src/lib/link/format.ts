// Presentation-only formatting. No business rules here.

export const money = (cents: number) =>
  (cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' })

/** "19:30" -> "7:30 PM" */
export function clock(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number)
  const suffix = h >= 12 ? 'PM' : 'AM'
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${suffix}`
}

const parseDate = (iso: string) => {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

/** "2026-12-04" -> "Fri, Dec 4" */
export const shortDate = (iso: string) =>
  parseDate(iso).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })

/** "2026-12-04" -> "Friday, December 4" */
export const longDate = (iso: string) =>
  parseDate(iso).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })

/** ISO timestamp -> local "21:04:12" */
export const timeOfDay = (iso: string | number) =>
  new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export const mmss = (ms: number) => {
  const s = Math.max(0, Math.ceil(ms / 1000))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
