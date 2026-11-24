import { PackageCheck, ShoppingCart, Truck } from 'lucide-react'
import { money } from '../../lib/link/format.ts'
import { useTween } from '../../lib/link/hooks.ts'
import type { LinkState } from '../../lib/link/reducer.ts'
import type { Cart, CartItem } from '../../lib/link/types.ts'

export function EmptyState({ icon: Icon, text }: { icon: typeof ShoppingCart; text: string }) {
  return (
    <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3 text-center" data-testid="empty-state">
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-accent-soft text-accent">
        <Icon size={20} strokeWidth={1.8} />
      </span>
      <p className="max-w-xs text-sm text-stone-500">{text}</p>
    </div>
  )
}

function CartLine({ item, change }: { item: CartItem; change?: { seq: number; kind: 'added' | 'qty' } }) {
  const qty = Math.round(useTween(item.qty, 240))
  const total = Math.round(useTween(item.line_total_cents, 240))
  return (
    <li
      data-testid={`cart-line-${item.sku}`}
      className={`relative -mx-3 px-3 py-4 ${change?.kind === 'added' ? 'animate-slide-in' : ''}`}
    >
      {change?.kind === 'qty' && (
        <span key={change.seq} className="pointer-events-none absolute inset-0 rounded-lg animate-flash-amber" aria-hidden />
      )}
      <div className="relative flex items-center gap-3 md:gap-5">
        <img
          src={item.image_path}
          alt=""
          className="h-14 w-[72px] shrink-0 rounded-lg border border-black/[0.06] bg-white object-cover md:h-[68px] md:w-[90px]"
        />
        <div className="min-w-0 flex-1">
          <div className="font-medium leading-snug text-stone-900">{item.name}</div>
          <div className="mt-0.5 text-sm text-stone-500 tabular-nums">{money(item.unit_price_cents)} each</div>
        </div>
        <div className="flex items-baseline gap-1 text-stone-500">
          <span className="text-sm">×</span>
          <span data-testid="qty" className="min-w-[2ch] text-right text-lg font-semibold text-stone-900 tabular-nums md:text-xl">
            {qty}
          </span>
        </div>
        <div
          data-testid="line-total"
          className="w-[92px] text-right text-lg leading-none font-semibold tracking-tight text-stone-900 tabular-nums md:w-40 md:text-[26px]"
        >
          {money(total)}
        </div>
      </div>
    </li>
  )
}

function Totals({ cart }: { cart: Cart }) {
  const subtotal = Math.round(useTween(cart.subtotal_cents))
  const total = Math.round(useTween(cart.total_cents))
  const progress = Math.min(1, cart.subtotal_cents / cart.free_shipping_threshold_cents)
  return (
    <dl className="space-y-2.5 pt-6 text-sm" data-testid="cart-totals">
      <div className="flex justify-between text-stone-600">
        <dt>Subtotal</dt>
        <dd className="tabular-nums">{money(subtotal)}</dd>
      </div>
      <div className="flex justify-between text-stone-600">
        <dt>Shipping</dt>
        <dd className={`tabular-nums ${cart.free_shipping ? 'font-medium text-accent' : ''}`} data-testid="shipping">
          {cart.free_shipping ? 'Free' : money(cart.shipping_cents)}
        </dd>
      </div>
      <div className="flex items-center gap-3 text-xs text-stone-500">
        <Truck size={14} className={cart.free_shipping ? 'text-accent' : 'text-stone-400'} aria-hidden />
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-black/[0.06]">
          <div className="h-full rounded-full bg-accent transition-[width] duration-250" style={{ width: `${progress * 100}%` }} />
        </div>
        <span className="text-right tabular-nums">
          {cart.free_shipping
            ? `Free shipping on orders over ${money(cart.free_shipping_threshold_cents)}`
            : `${money(cart.amount_to_free_shipping_cents)} more for free shipping`}
        </span>
      </div>
      <div className="flex items-baseline justify-between border-t border-black/[0.06] pt-3 text-stone-900">
        <dt className="font-medium">Total</dt>
        <dd className="text-xl font-semibold tabular-nums" data-testid="cart-total">
          {money(total)}
        </dd>
      </div>
    </dl>
  )
}

export function CartView({ state, agentName }: { state: LinkState; agentName: string }) {
  const cart = state.cart
  if (!cart || cart.items.length === 0)
    return <EmptyState icon={ShoppingCart} text={`Your cart will appear here as you talk with ${agentName}.`} />
  const placed = cart.order?.status === 'placed'
  return (
    <div className="flex h-full flex-col" data-testid="cart">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold tracking-tight text-stone-900">Your cart</h2>
        {placed ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-accent px-2.5 py-0.5 text-xs font-medium text-white">
            <PackageCheck size={13} /> Order #{cart.order?.id} placed
          </span>
        ) : (
          <span className="text-sm text-stone-500 tabular-nums">
            {cart.item_count} {cart.item_count === 1 ? 'item' : 'items'}
          </span>
        )}
      </div>
      <ul className="mt-3 divide-y divide-black/[0.06] border-y border-black/[0.06]">
        {cart.items.map((item) => (
          <CartLine key={item.sku} item={item} change={state.cartChanges[item.sku]} />
        ))}
      </ul>
      <Totals cart={cart} />
    </div>
  )
}
