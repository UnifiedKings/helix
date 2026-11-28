import type { QueueItem } from '../api/types'
import { traceMedia } from './mediaDiagnostics'

type Slot = { audio: HTMLAudioElement; id: string }
type Transition = { slot: Slot; generation: number; timer: number | null; trying: boolean; initialTried: boolean; readyTried: boolean; timerTried: boolean; timerDue: boolean; readyDue: boolean }
type Callbacks = { playing: (value: boolean) => void; progress: (time: number, duration: number) => void; ended: (id: string) => void; error: (message: string) => void; active: (audio: HTMLAudioElement) => void; interrupted: () => void }
const FALLBACK_MS = 250
const stream = (id: string) => `/api/stream/${encodeURIComponent(id)}`
const key = (id: string) => `helix.playback.position.${id}`
function saved(id: string) { try { return Math.max(0, Number(localStorage.getItem(key(id))) || 0) } catch { return 0 } }
function remember(id: string, time: number) { try { if (id && Number.isFinite(time)) localStorage.setItem(key(id), String(time)) } catch {} }

/** Two persistent media elements; only a backend-selected track is ever loaded.
 * The old element stays alive until the new one actually starts, then is retired.
 * No speculative streams, silent looping tracks, or early fulfillment requests.
 */
export class PlaybackEngine {
  private slots: Slot[]
  private activeSlot: Slot
  private selected: Slot
  private transition: Transition | null = null
  private generation = 0
  private disposed = false
  private listeners: Array<() => void> = []
  private restore = 0
  private endedId = ''
  private wants = false
  private skipping = false
  private lastSavedAt = 0
  constructor(first: HTMLAudioElement, second: HTMLAudioElement, private callbacks: Callbacks) {
    this.slots = [first, second].map(audio => ({audio, id: ''}))
    this.selected = this.activeSlot = this.slots[0]
    for (const slot of this.slots) {
      const audio = slot.audio
      const on = (name: string, fn: () => void) => { audio.addEventListener(name, fn); this.listeners.push(() => audio.removeEventListener(name, fn)) }
      on('canplay', () => { const t = this.transition; if (t?.slot === slot && audio.readyState >= 3 && this.matches(slot)) { t.readyDue = true; this.attempt(t) } })
      on('playing', () => {
        if (this.skipping) { audio.pause(); return }
        if (slot !== this.selected || !this.wants || !this.matches(slot) || (slot !== this.activeSlot && !this.transition)) { if (slot !== this.activeSlot || !this.wants) audio.pause(); return }
        this.handoff(slot)
      })
      on('pause', () => {
        if (this.skipping || slot !== this.selected || !audio.paused) return
        if (!this.wants) { this.callbacks.playing(false); return }
        // Hardware/browser pauses that arrive as native events rather than an
        // action handler must not leave the UI claiming to be playing.
        if (slot === this.activeSlot && !this.transition && !audio.ended && this.matches(slot)) {
          this.wants = false; this.callbacks.playing(false); this.callbacks.interrupted()
        }
      })
      on('loadedmetadata', () => {
        if (slot !== this.selected || !this.matches(slot)) return
        if (this.restore > 0 && Number.isFinite(audio.duration)) { try { audio.currentTime = Math.min(this.restore, Math.max(0, audio.duration - 3)) } catch {} }
        this.restore = 0; this.report(slot)
      })
      for (const event of ['timeupdate', 'durationchange', 'seeked']) on(event, () => { if (slot === this.selected && this.matches(slot)) this.report(slot) })
      on('ended', () => {
        if (this.skipping || slot !== this.selected || !audio.ended || !this.wants || this.endedId === slot.id || !this.matches(slot)) return
        this.endedId = slot.id; remember(slot.id, 0); this.callbacks.ended(slot.id)
      })
      on('error', () => { if (slot === this.selected && slot.id && audio.error) this.fail(`Audio playback error ${audio.error.code}`) })
    }
  }
  get id() { return this.selected.id }
  get audio() { return this.selected.audio }
  get playing() { return this.wants }
  private matches(slot: Slot) { return !!slot.id && slot.audio.currentSrc === new URL(stream(slot.id), window.location.origin).href }
  private report(slot: Slot) { const a = slot.audio; const time = Number.isFinite(a.currentTime) ? a.currentTime : 0; const duration = Number.isFinite(a.duration) ? a.duration : 0; this.callbacks.progress(time, duration); if (Date.now() - this.lastSavedAt >= 1000) { this.lastSavedAt = Date.now(); remember(slot.id, time) } }
  private cancel() { ++this.generation; if (this.transition?.timer != null) window.clearTimeout(this.transition.timer); this.transition = null }
  private retire(slot: Slot) { slot.audio.pause(); slot.id = ''; slot.audio.removeAttribute('src'); slot.audio.load() }
  private handoff(slot: Slot) {
    if (this.disposed || !this.wants || slot !== this.selected) return
    if (!this.transition) return
    const previous = this.activeSlot; this.activeSlot = slot; this.cancel(); this.callbacks.active(slot.audio)
    if (previous !== slot) this.retire(previous)
    this.callbacks.playing(true); this.report(slot); traceMedia('engine-handoff', slot.audio)
  }
  cancelPending() {
    if (!this.transition) return
    this.cancel()
    if (this.selected !== this.activeSlot) this.selected.audio.pause()
  }
  stopForSkip() {
    // Preserve play intent and the registered session while the backend selects
    // the next item, but stop audible playback immediately.
    this.skipping = true
    this.cancel()
    for (const slot of this.slots) {
      if (slot.id) remember(slot.id, slot.audio.currentTime)
      slot.audio.pause()
    }
    traceMedia('engine-skip-local-stop', this.audio)
  }
  private fail(message: string) { this.pause(); this.callbacks.error(message) }
  private attempt(t: Transition) {
    if (this.disposed || this.transition !== t || t.generation !== this.generation || !this.wants || t.trying) return
    if (!t.initialTried) t.initialTried = true
    else if (t.readyDue && !t.readyTried) t.readyTried = true
    else if (t.timerDue && !t.timerTried) t.timerTried = true
    else return
    t.trying = true
    traceMedia('engine-play-attempt', t.slot.audio, { readiness: t.readyTried, fallback: t.timerTried })
    void t.slot.audio.play().then(() => {
      if (this.transition === t && this.wants && !t.slot.audio.paused) this.handoff(t.slot)
      else if (this.skipping || !this.wants || t.slot !== this.selected) t.slot.audio.pause()
    }).catch(error => {
      if (this.transition !== t) return
      t.trying = false
      if (t.initialTried && t.readyTried && t.timerTried) this.fail(error instanceof Error ? error.message : 'Could not start playback')
      else this.attempt(t)
    })
  }
  private start() {
    if (!this.selected.id || this.disposed) return
    this.cancel(); this.skipping = false; this.wants = true; this.callbacks.playing(true)
    const slot = this.selected
    const t: Transition = { slot, generation: this.generation, timer: null, trying: false, initialTried: false, readyTried: false, timerTried: false, timerDue: false, readyDue: false }
    this.transition = t
    t.timer = window.setTimeout(() => { t.timer = null; t.timerDue = true; this.attempt(t) }, FALLBACK_MS)
    // play() requests playback immediately, even while the media is loading.
    // Readiness and the original timer remain independent recovery paths.
    if (this.matches(slot) && slot.audio.readyState >= 3) t.readyDue = true
    this.attempt(t)
  }
  select(track: QueueItem | null, play: boolean, restore = false) {
    if (this.disposed) return
    this.skipping = false
    if (!track) { this.pause(); this.slots.forEach(s => this.retire(s)); this.selected = this.activeSlot = this.slots[0]; this.callbacks.active(this.selected.audio); this.callbacks.progress(0, 0); return }
    if (track.id !== this.selected.id) {
      this.cancel(); this.endedId = ''; this.lastSavedAt = 0
      // Keep the previous element available until handoff. Never
      // fetch a queue item before the backend selects it as now_playing.
      const slot = !this.activeSlot.id && !this.selected.id ? this.activeSlot : this.slots.find(s => s !== this.activeSlot)!
      this.retire(slot); this.selected = slot; slot.id = track.id; this.restore = restore ? saved(track.id) : 0
      if (!play) { this.slots.forEach(s => s.audio.pause()); if (this.activeSlot !== slot) this.retire(this.activeSlot); this.activeSlot = slot }
      this.wants = play; slot.audio.src = stream(track.id); slot.audio.load()
      this.callbacks.active(slot.audio); this.callbacks.progress(this.restore, 0)
      if (play) this.start(); else this.callbacks.playing(false)
      return
    }
    if (!play) { this.pause(); return }
    if (this.wants && (this.transition || (!this.audio.paused && !this.audio.ended))) return
    if (this.audio.ended) { try { this.audio.currentTime = 0 } catch {} this.endedId = '' }
    this.start()
  }
  pause() { this.cancel(); this.skipping = false; this.wants = false; for (const slot of this.slots) { if (slot.id) remember(slot.id, slot.audio.currentTime); slot.audio.pause() } if (this.activeSlot !== this.selected) { this.retire(this.activeSlot); this.activeSlot = this.selected } this.callbacks.playing(false); traceMedia('engine-pause', this.audio) }
  seek(seconds: number) { const a = this.audio; if (!Number.isFinite(seconds) || !Number.isFinite(a.duration) || a.duration <= 0) return; try { a.currentTime = Math.max(0, Math.min(a.duration, seconds)); this.report(this.selected) } catch {} }
  volume(value: number) { for (const slot of this.slots) slot.audio.volume = Math.max(0, Math.min(1, value)) }
  repeat(value: boolean) { for (const slot of this.slots) slot.audio.loop = value }
  dispose() { this.disposed = true; this.cancel(); this.listeners.forEach(fn => fn()); this.slots.forEach(s => this.retire(s)) }
}
