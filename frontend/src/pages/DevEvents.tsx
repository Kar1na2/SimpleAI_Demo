import { useEffect, useRef } from 'react'
import { useParams } from 'react-router'
import { type ConnectionStatus, useCallEvents } from '../lib/useCallEvents.ts'

const STATUS_STYLE: Record<ConnectionStatus, string> = {
  connecting: 'bg-amber-100 text-amber-800',
  open: 'bg-emerald-100 text-emerald-800',
  reconnecting: 'bg-amber-100 text-amber-800',
  not_found: 'bg-red-100 text-red-800',
}

const emitHint = (callId = '<id>') =>
  `curl -X POST localhost:8000/api/dev/calls/${callId}/emit -H 'content-type: application/json' -d '{"type":"note.added","payload":{"text":"hello"}}'`

export default function DevEvents() {
  const { callId } = useParams()
  const { events, status } = useCallEvents(callId)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [events.length])

  return (
    <main className="mx-auto max-w-4xl p-6 font-mono text-sm text-stone-800">
      <header className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-base font-semibold">Call events</h1>
        <code className="rounded bg-stone-100 px-2 py-0.5 text-xs text-stone-600">{callId}</code>
        <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[status]}`} data-testid="ws-status">
          {status === 'not_found' ? 'call not found' : status}
        </span>
        <span className="ml-auto text-xs text-stone-500">{events.length} events</span>
      </header>

      {events.length === 0 ? (
        <p className="rounded border border-dashed border-stone-300 p-6 text-center text-stone-500">
          No events yet. Emit one with:
          <br />
          <code className="mt-2 inline-block text-xs">{emitHint(callId)}</code>
        </p>
      ) : (
        <ol className="divide-y divide-stone-200 rounded border border-stone-200" data-testid="event-list">
          {events.map((e) => (
            <li key={e.seq} className="grid grid-cols-[3rem_7rem_1fr] gap-3 px-3 py-2">
              <span className="text-stone-400">#{e.seq}</span>
              <span className="text-stone-500">{new Date(e.created_at).toLocaleTimeString()}</span>
              <div className="min-w-0">
                <div className="font-semibold">{e.type}</div>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words text-xs text-stone-600">
                  {JSON.stringify(e.payload, null, 2)}
                </pre>
              </div>
            </li>
          ))}
        </ol>
      )}
      <div ref={endRef} />
    </main>
  )
}
