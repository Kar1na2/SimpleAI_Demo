// Director audio: TTS clips in order, a US ringback tone while ringing, a click at Connected.
// Everything goes through one AudioContext, unlocked by the Start click (browser autoplay rules).

export type Speaking = { name: string; speaker: 'agent' | 'customer'; turnId: number | null } | null
type Clip = { url: string; name: string; speaker: 'agent' | 'customer'; turnId: number | null }

export class AudioEngine {
  private ctx: AudioContext | null = null
  private el: HTMLAudioElement | null = null
  private analyser: AnalyserNode | null = null
  private queue: Clip[] = []
  private playing = false
  private ring: { oscs: OscillatorNode[]; gain: GainNode; timer: number } | null = null
  private levelBuf: Uint8Array<ArrayBuffer> | null = null
  speaking: Speaking = null
  onSpeakingChange: (s: Speaking) => void = () => {}

  /** Call from the Start click handler (a user gesture). */
  async unlock(): Promise<void> {
    if (!this.ctx) {
      this.ctx = new AudioContext()
      this.el = new Audio()
      this.el.preload = 'auto'
      const source = this.ctx.createMediaElementSource(this.el)
      this.analyser = this.ctx.createAnalyser()
      this.analyser.fftSize = 512
      this.levelBuf = new Uint8Array(new ArrayBuffer(this.analyser.fftSize))
      source.connect(this.analyser)
      this.analyser.connect(this.ctx.destination)
      this.el.addEventListener('ended', () => this.next())
      this.el.addEventListener('error', () => this.next())
    }
    if (this.ctx.state !== 'running') await this.ctx.resume()
  }

  /** 0..1 loudness of whatever is playing right now (for the level bar). */
  level(): number {
    if (!this.analyser || !this.levelBuf || !this.playing) return 0
    this.analyser.getByteTimeDomainData(this.levelBuf)
    let sum = 0
    for (const v of this.levelBuf) sum += ((v - 128) / 128) ** 2
    return Math.min(1, Math.sqrt(sum / this.levelBuf.length) * 4)
  }

  enqueue(clip: Clip): void {
    this.queue.push(clip)
    if (!this.playing) this.next()
  }

  private next(): void {
    const clip = this.queue.shift()
    if (!clip || !this.el) {
      this.playing = false
      this.setSpeaking(null)
      return
    }
    this.playing = true
    this.setSpeaking({ name: clip.name, speaker: clip.speaker, turnId: clip.turnId })
    this.el.src = clip.url
    this.el.play().catch(() => this.next())
  }

  private setSpeaking(s: Speaking): void {
    this.speaking = s
    this.onSpeakingChange(s)
  }

  /** US ringback: 440 Hz + 480 Hz, 2 s on, 4 s off, until stopRing(). */
  startRing(): void {
    if (!this.ctx || this.ring) return
    const ctx = this.ctx
    const gain = ctx.createGain()
    gain.gain.value = 0
    gain.connect(ctx.destination)
    const oscs = [440, 480].map((f) => {
      const o = ctx.createOscillator()
      o.frequency.value = f
      o.connect(gain)
      o.start()
      return o
    })
    let next = ctx.currentTime + 0.05
    const schedule = () => {
      while (next < ctx.currentTime + 12) {
        gain.gain.setValueAtTime(0, next)
        gain.gain.linearRampToValueAtTime(0.07, next + 0.02)
        gain.gain.setValueAtTime(0.07, next + 1.98)
        gain.gain.linearRampToValueAtTime(0, next + 2)
        next += 6
      }
    }
    schedule()
    this.ring = { oscs, gain, timer: window.setInterval(schedule, 3000) }
  }

  stopRing(): void {
    if (!this.ring || !this.ctx) return
    const { oscs, gain, timer } = this.ring
    window.clearInterval(timer)
    gain.gain.cancelScheduledValues(this.ctx.currentTime)
    gain.gain.setValueAtTime(0, this.ctx.currentTime)
    oscs.forEach((o) => o.stop(this.ctx!.currentTime + 0.05))
    this.ring = null
  }

  /** A short line-pickup click. */
  click(): void {
    if (!this.ctx) return
    const ctx = this.ctx
    const len = Math.floor(ctx.sampleRate * 0.03)
    const buf = ctx.createBuffer(1, len, ctx.sampleRate)
    const data = buf.getChannelData(0)
    for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len) ** 3 * 0.6
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(ctx.destination)
    src.start()
  }

  reset(): void {
    this.stopRing()
    this.queue = []
    if (this.el) {
      this.el.pause()
      this.el.removeAttribute('src')
    }
    this.playing = false
    this.setSpeaking(null)
  }
}
