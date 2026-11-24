import { Link2Off, Lock } from 'lucide-react'
import { BrandMark } from './Header.tsx'

/** The link is dead: brand only, no customer data. */
export function ExpiredScreen({ brand, fade }: { brand: string | null; fade: boolean }) {
  return (
    <main className={`flex min-h-dvh items-center justify-center bg-surface px-6 ${fade ? 'animate-fade-in' : ''}`} data-testid="expired">
      <div className="w-full max-w-sm text-center">
        {brand && (
          <div className="mb-10 flex items-center justify-center gap-2.5">
            <BrandMark name={brand} />
            <span className="text-[15px] font-semibold tracking-tight text-stone-900">{brand}</span>
          </div>
        )}
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent ring-1 ring-accent-line">
          <Lock size={20} strokeWidth={2} />
        </span>
        <h1 className="mt-5 text-xl font-semibold tracking-tight text-stone-900">This link has expired</h1>
        <p className="mt-2 text-sm text-stone-500">Your call has ended. A confirmation was sent by text.</p>
      </div>
    </main>
  )
}

export function InvalidScreen() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-stone-50 px-6" data-testid="invalid">
      <div className="text-center">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-stone-100 text-stone-500">
          <Link2Off size={20} />
        </span>
        <h1 className="mt-5 text-xl font-semibold tracking-tight text-stone-900">This link isn't valid.</h1>
      </div>
    </main>
  )
}

export function LoadingScreen() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-stone-50" data-testid="loading">
      <div className="flex items-center gap-2 text-sm text-stone-500">
        <span className="h-2 w-2 rounded-full bg-stone-400 animate-typing-dot" /> Connecting to your call…
      </div>
    </main>
  )
}
