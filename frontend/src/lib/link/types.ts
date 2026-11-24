// Shapes of GET /api/s/{token} and the call events streamed on /ws/s/{token}.
// Business values (totals, shipping, booked vs pending) come from the backend as-is.

export type Theme = { accent?: string; surface?: string }

export type CartItem = {
  sku: string
  name: string
  qty: number
  unit_price_cents: number
  line_total_cents: number
  image_path: string
}

export type Cart = {
  items: CartItem[]
  item_count: number
  subtotal_cents: number
  shipping_cents: number
  total_cents: number
  free_shipping_threshold_cents: number
  free_shipping: boolean
  amount_to_free_shipping_cents: number
  order: { id: number; status: 'draft' | 'placed'; total_cents: number } | null
}

export type ReservationFields = { party_size: number; date: string; weekday: string; time: string }

export type ReservationChange = {
  field: 'party_size' | 'date' | 'time'
  from: number | string
  to: number | string
}

export type Reservation = ReservationFields & {
  id: number
  customer_name: string
  status: 'booked' | 'pending_change' | 'confirmed'
  pending: boolean
  booked: ReservationFields
  changes: ReservationChange[]
}

export type Slot = { date: string; time: string; status: 'open' | 'full' }

export type Snapshot = {
  seq: number
  call_id: string
  brand: string | null
  agent_name: string | null
  theme: Theme
  scenario: string
  direction: string
  status: string
  connected_at: string | null
  link_created_at: string
  expires_at: string
  cart: Cart | null
  reservation: Reservation | null
  slots: Slot[] | null
}

export type CallEvent = {
  id: number
  call_id: string
  seq: number
  type: string
  // Each event type has its own payload; the reducer reads the fields it needs.
  payload: Record<string, any>
  created_at: string
}
