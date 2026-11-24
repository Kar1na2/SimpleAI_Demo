import { useEffect, useRef, useState } from 'react'

/** Current time, refreshed every `intervalMs`. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

/** Animates from the shown value to a new one over `ms` (ease-out). First render: no animation. */
export function useTween(value: number, ms = 240): number {
  const [shown, setShown] = useState(value)
  const shownRef = useRef(value)
  useEffect(() => {
    const from = shownRef.current
    if (from === value) return
    const start = performance.now()
    let raf = 0
    const step = (t: number) => {
      const k = Math.min(1, (t - start) / ms)
      const v = from + (value - from) * (1 - (1 - k) ** 3)
      shownRef.current = v
      setShown(v)
      if (k < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value, ms])
  return shown
}

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    mql.addEventListener('change', onChange)
    onChange()
    return () => mql.removeEventListener('change', onChange)
  }, [query])
  return matches
}

/** True once Date.now() >= at (re-renders exactly then). False when `at` is null. */
export function useReached(at: number | null): boolean {
  const [reached, setReached] = useState(false)
  useEffect(() => {
    if (at == null) return setReached(false)
    const wait = at - Date.now()
    if (wait <= 0) return setReached(true)
    setReached(false)
    const id = window.setTimeout(() => setReached(true), wait)
    return () => window.clearTimeout(id)
  }, [at])
  return reached
}
