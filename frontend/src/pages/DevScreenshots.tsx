// Dev only: browse frontend/public/screenshots (written by `npm run screenshots`).
// The list comes from the Vite dev server (/__dev/screenshots.json), newest first.
import { useCallback, useEffect, useState } from 'react'

type Shot = { name: string; url: string; mtime: number; size: number }

const when = (ms: number) =>
  new Date(ms).toLocaleString('en-GB', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })

export default function DevScreenshots() {
  const [shots, setShots] = useState<Shot[] | null>(null)
  const [open, setOpen] = useState<number | null>(null)

  const load = useCallback(async () => {
    const r = await fetch('/__dev/screenshots.json', { cache: 'no-store' })
    setShots(r.ok ? ((await r.json()) as Shot[]) : [])
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (open == null || !shots) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(null)
      if (e.key === 'ArrowRight') setOpen((i) => (i == null ? i : Math.min(shots.length - 1, i + 1)))
      if (e.key === 'ArrowLeft') setOpen((i) => (i == null ? i : Math.max(0, i - 1)))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, shots])

  const src = (s: Shot) => `${s.url}?v=${Math.round(s.mtime)}` // cache-bust re-runs that overwrite a file

  return (
    <main className="min-h-dvh bg-stone-50 px-8 py-8 text-stone-800">
      <header className="mb-6 flex items-baseline justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Screenshots</h1>
          <p className="text-sm text-stone-500">
            frontend/public/screenshots · newest first · {shots?.length ?? '…'} files
          </p>
        </div>
        <button onClick={load} className="rounded-lg border border-black/10 bg-white px-3 py-1.5 text-sm hover:bg-stone-100">
          Refresh
        </button>
      </header>

      {shots && shots.length === 0 && (
        <p className="rounded-lg border border-dashed border-stone-300 p-8 text-center text-sm text-stone-500">
          No screenshots yet. Run <code>make screenshots</code> with the backend and frontend running.
        </p>
      )}

      <ul className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-5" data-testid="screenshot-grid">
        {shots?.map((s, i) => (
          <li key={s.name}>
            <button
              onClick={() => setOpen(i)}
              className="group block w-full overflow-hidden rounded-xl border border-black/[0.08] bg-white text-left transition-colors duration-150 hover:border-black/20"
            >
              <div className="aspect-[16/10] overflow-hidden bg-stone-100">
                <img src={src(s)} alt={s.name} loading="lazy" className="h-full w-full object-cover object-top" />
              </div>
              <div className="flex items-baseline justify-between gap-3 px-3 py-2">
                <span className="truncate text-sm font-medium">{s.name}</span>
                <span className="shrink-0 text-xs text-stone-400 tabular-nums">{when(s.mtime)}</span>
              </div>
            </button>
          </li>
        ))}
      </ul>

      {open != null && shots?.[open] && (
        <div
          className="fixed inset-0 z-50 flex flex-col bg-black/85 animate-fade-in"
          onClick={() => setOpen(null)}
          role="dialog"
          aria-label={shots[open].name}
        >
          <div className="flex items-center justify-between px-5 py-3 text-sm text-white/80" onClick={(e) => e.stopPropagation()}>
            <span>
              {shots[open].name} · {open + 1}/{shots.length} · ← → to browse, Esc to close
            </span>
            <a href={src(shots[open])} target="_blank" rel="noreferrer" className="underline-offset-2 hover:underline">
              Open original
            </a>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-4">
            <img src={src(shots[open])} alt={shots[open].name} className="mx-auto h-auto max-w-none" onClick={(e) => e.stopPropagation()} />
          </div>
        </div>
      )}
    </main>
  )
}
