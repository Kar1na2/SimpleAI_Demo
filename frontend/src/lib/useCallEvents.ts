import { useEffect, useState } from 'react'

export type CallEvent = {
  id: number
  call_id: string
  seq: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

export type ConnectionStatus = 'connecting' | 'open' | 'reconnecting' | 'not_found'

const CLOSE_CALL_NOT_FOUND = 4404
const MAX_BACKOFF_MS = 10_000

/**
 * Subscribes to /ws/calls/:callId. The server replays history (seq > since) and then
 * streams live events. On disconnect it reconnects with since=<last seq seen>, so no
 * event is missed or shown twice.
 */
export function useCallEvents(callId: string | undefined) {
  const [events, setEvents] = useState<CallEvent[]>([])
  const [status, setStatus] = useState<ConnectionStatus>('connecting')

  useEffect(() => {
    if (!callId) return
    let ws: WebSocket | null = null
    let lastSeq = 0
    let attempt = 0
    let retryTimer: number | undefined
    let disposed = false

    setEvents([])
    setStatus('connecting')

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const url = `${proto}://${location.host}/ws/calls/${encodeURIComponent(callId)}?since=${lastSeq}`
      const socket = new WebSocket(url)
      ws = socket

      socket.onopen = () => {
        attempt = 0
        setStatus('open')
      }
      socket.onmessage = (msg) => {
        const event = JSON.parse(msg.data as string) as CallEvent
        if (event.seq <= lastSeq) return
        lastSeq = event.seq
        setEvents((prev) => [...prev, event])
      }
      socket.onclose = (e) => {
        if (disposed || ws !== socket) return
        ws = null
        if (e.code === CLOSE_CALL_NOT_FOUND) {
          setStatus('not_found')
          return
        }
        setStatus('reconnecting')
        const delay = Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempt++)
        retryTimer = window.setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      disposed = true
      window.clearTimeout(retryTimer)
      ws?.close()
    }
  }, [callId])

  return { events, status }
}
