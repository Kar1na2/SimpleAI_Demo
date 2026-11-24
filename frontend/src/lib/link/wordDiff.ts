export type DiffPart = { text: string; op: 'same' | 'del' | 'add' }

const norm = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}']/gu, '')

/**
 * Word-level diff (LCS) from what the agent heard to what was actually said.
 * "...like ten of the ribeyes." vs "...like two of the ribeyes." -> ten (del), two (add).
 */
export function wordDiff(heard: string, said: string): DiffPart[] {
  const a = heard.split(/\s+/).filter(Boolean)
  const b = said.split(/\s+/).filter(Boolean)
  const n = a.length
  const m = b.length
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      lcs[i][j] = norm(a[i]) === norm(b[j]) ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])

  const out: DiffPart[] = []
  const push = (text: string, op: DiffPart['op']) => {
    const last = out[out.length - 1]
    if (last && last.op === op) last.text += ` ${text}`
    else out.push({ text, op })
  }
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (norm(a[i]) === norm(b[j])) {
      push(b[j], 'same') // keep the corrected spelling/punctuation
      i++
      j++
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) push(a[i++], 'del')
    else push(b[j++], 'add')
  }
  while (i < n) push(a[i++], 'del')
  while (j < m) push(b[j++], 'add')
  return out
}
