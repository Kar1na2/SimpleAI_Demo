import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { initialLinkState, linkReducer } from './reducer.ts'
import type { CallEvent, Snapshot, Theme } from './types.ts'

export type Phase = 'loading' | 'live' | 'expiring' | 'expired' | 'invalid'

const CLOSE_NOT_FOUND = 4404
const CLOSE_LINK_GONE = 4410
const MAX_BACKOFF_MS = 10_000
const FADE_MS = 250

/** Close without logging "closed before the connection is established" (StrictMode remounts). */
function closeQuietly(ws: WebSocket) {
  ws.onmessage = ws.onclose = ws.onerror = null
  if (ws.readyState === WebSocket.CONNECTING) ws.onopen = () => ws.close()
  else ws.close()
}

/**
 * The link page's connection to the call: GET /api/s/{token}, then WS /ws/s/{token}?since=N.
 * 404 -> invalid. 410 or close 4410 -> expired (fading out first if the page was live).
 * Other disconnects reconnect with backoff, resuming after the last seq received.
 */
export function useLinkSession(token: string) {
  const [state, dispatch] = useReducer(linkReducer, initialLinkState)
  const [phase, setPhase] = useState<Phase>('loading')
  const [connected, setConnected] = useState(false)
  // Brand + colors survive expiry so the expired screen can say who it was. No customer data.
  const [brand, setBrand] = useState<{ name: string | null; theme: Theme }>({ name: null, theme: {} })
  const phaseRef = useRef<Phase>('loading')
  const expireRef = useRef<() => void>(() => {})

  const setPhaseBoth = (p: Phase) => {
    phaseRef.current = p
    setPhase(p)
  }

  useEffect(() => {
    let disposed = false
    let ws: WebSocket | null = null
    let timer: number | undefined
    let attempt = 0
    let lastSeq = 0

    const backoff = () => Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempt++)

    const expire = () => {
      if (disposed || phaseRef.current === 'expiring' || phaseRef.current === 'expired') return
      if (ws) closeQuietly(ws)
      setConnected(false)
      if (phaseRef.current !== 'live') {
        dispatch({ type: 'reset' })
        setPhaseBoth('expired')
        return
      }
      setPhaseBoth('expiring') // fade the live page out, then drop every bit of call data
      timer = window.setTimeout(() => {
        dispatch({ type: 'reset' })
        setPhaseBoth('expired')
      }, FADE_MS)
    }
    expireRef.current = expire

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${proto}://${location.host}/ws/s/${encodeURIComponent(token)}?since=${lastSeq}`)
      ws = socket
      socket.onopen = () => {
        attempt = 0
        setConnected(true)
      }
      socket.onmessage = (msg) => {
        const event = JSON.parse(msg.data as string) as CallEvent
        lastSeq = Math.max(lastSeq, event.seq)
        dispatch({ type: 'event', event })
      }
      socket.onclose = (e) => {
        if (disposed || ws !== socket) return
        ws = null
        setConnected(false)
        if (e.code === CLOSE_LINK_GONE) return expire()
        if (e.code === CLOSE_NOT_FOUND) return setPhaseBoth('invalid')
        timer = window.setTimeout(connect, backoff())
      }
    }

    const load = async () => {
      try {
        const r = await fetch(`/api/s/${encodeURIComponent(token)}`)
        if (disposed) return
        if (r.status === 404) return setPhaseBoth('invalid')
        if (r.status === 410) {
          const detail = (await r.json().catch(() => ({}))).detail ?? {}
          setBrand({ name: detail.brand ?? null, theme: detail.theme ?? {} })
          return setPhaseBoth('expired')
        }
        if (!r.ok) throw new Error(`snapshot ${r.status}`)
        const snapshot = (await r.json()) as Snapshot
        if (disposed) return
        setBrand({ name: snapshot.brand, theme: snapshot.theme ?? {} })
        dispatch({ type: 'snapshot', snapshot })
        setPhaseBoth('live')
        connect()
      } catch {
        if (!disposed) timer = window.setTimeout(load, backoff())
      }
    }

    load()
    return () => {
      disposed = true
      window.clearTimeout(timer)
      if (ws) closeQuietly(ws)
    }
  }, [token])

  /** For the countdown: the server also closes the socket at expiry; this covers a stalled socket. */
  const expireNow = useCallback(() => expireRef.current(), [])

  return { state, phase, connected, brand, expireNow }
}
