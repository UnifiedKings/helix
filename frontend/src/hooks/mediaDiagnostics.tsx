import { useEffect, useRef, useState, useSyncExternalStore, type RefObject } from 'react'
import { createPortal } from 'react-dom'

const KEY = 'helix.media.diagnostics.v1'
const ENABLED_KEY = KEY + '.enabled'
const MAX = 600

type Entry = {
  at: string; elapsedMs?: number; event: string; paused?: boolean; ended?: boolean
  ready?: number; network?: number; position?: number; visibility: string
  sessionState?: string; extra?: Record<string, unknown>
}

function initialEnabled() {
  try {
    const flag = new URLSearchParams(window.location.search).get('mediaDebug')
    if (flag !== null) {
      const value = flag === '0' ? '0' : '1'
      localStorage.setItem(ENABLED_KEY, value)
      sessionStorage.removeItem(ENABLED_KEY)
      return value === '1'
    }
    return (localStorage.getItem(ENABLED_KEY) ?? sessionStorage.getItem(ENABLED_KEY)) === '1'
  } catch { return false }
}
let enabled = initialEnabled()
let rows: Entry[] | null = null
let flushTimer: number | null = null
const subscribers = new Set<() => void>()

function subscribe(listener: () => void) {
  subscribers.add(listener)
  return () => { subscribers.delete(listener) }
}
function getEnabled() { return enabled }
function read(): Entry[] {
  if (rows !== null) return rows
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || '[]')
    rows = Array.isArray(value) ? value.slice(-MAX) : []
  } catch { rows = [] }
  return rows
}
function flush() {
  if (flushTimer !== null) { window.clearTimeout(flushTimer); flushTimer = null }
  if (rows === null) return
  try { localStorage.setItem(KEY, JSON.stringify(rows)) } catch { /* retain in-memory log */ }
}
function scheduleFlush() {
  if (flushTimer === null) flushTimer = window.setTimeout(flush, 300)
}
function sessionState() {
  try { return navigator.mediaSession?.playbackState } catch { return undefined }
}
// Errors can contain request URLs. Do not retain URL strings or token-like query parameters.
function safeExtra(extra?: Record<string, unknown>) {
  if (!extra) return undefined
  return Object.fromEntries(Object.entries(extra).map(([key, value]) => [key,
    typeof value === 'string' ? value.replace(/https?:\/\/[^\s]+/gi, '[URL]').replace(/([?&](?:token|password|key|auth|t|s)=)[^\s&]+/gi, '$1[redacted]').slice(0, 300) : value
  ]))
}
export function traceMedia(event: string, audio?: HTMLAudioElement | null, extra?: Record<string, unknown>) {
  if (!enabled) return
  try {
    const list = read()
    list.push({ at: new Date().toISOString(), elapsedMs: Math.round(performance.now()), event,
      paused: audio?.paused, ended: audio?.ended, ready: audio?.readyState, network: audio?.networkState,
      position: audio && Number.isFinite(audio.currentTime) ? Math.round(audio.currentTime * 10) / 10 : undefined,
      visibility: document.visibilityState, sessionState: sessionState(), extra: safeExtra(extra) })
    if (list.length > MAX) list.splice(0, list.length - MAX)
    scheduleFlush()
  } catch { /* diagnostics must never affect playback */ }
}
export function setMediaDiagnosticsEnabled(value: boolean) {
  if (enabled === value) return
  if (!value) { traceMedia('diagnostics-disabled'); flush() }
  enabled = value
  try { localStorage.setItem(ENABLED_KEY, value ? '1' : '0'); sessionStorage.removeItem(ENABLED_KEY) } catch {}
  if (value) { traceMedia('diagnostics-enabled'); flush() }
  for (const listener of subscribers) listener()
}
export function clearMediaDiagnostics() {
  if (flushTimer !== null) { window.clearTimeout(flushTimer); flushTimer = null }
  rows = []
  try { localStorage.removeItem(KEY) } catch {}
}
export function getMediaDiagnosticsReport() {
  const list = read()
  flush()
  return JSON.stringify({ version: 2, capturedAt: new Date().toISOString(), enabled,
    userAgent: navigator.userAgent, standalone: window.matchMedia('(display-mode: standalone)').matches,
    eventCount: list.length, events: list }, null, 2)
}

// Audio listeners stay independent of the settings UI, including while the PWA is locked.
export function MediaDiagnostics({ audioRef, secondaryAudioRef }: { audioRef: RefObject<HTMLAudioElement | null>; secondaryAudioRef?: RefObject<HTMLAudioElement | null> }) {
  const capture = useSyncExternalStore(subscribe, getEnabled)
  useEffect(() => {
    if (!capture) return
    const elements = [audioRef.current, secondaryAudioRef?.current].filter((a): a is HTMLAudioElement => Boolean(a))
    const events = ['play', 'playing', 'pause', 'ended', 'emptied', 'loadstart', 'loadedmetadata', 'canplay', 'waiting', 'stalled', 'abort', 'error', 'seeked', 'suspend']
    const log = (event: Event) => {
      const audio = event.currentTarget as HTMLAudioElement
      traceMedia(event.type, audio, { slot: elements.indexOf(audio), ...(audio.error ? { errorCode: audio.error.code } : {}) })
    }
    const visibility = () => {
      traceMedia('visibilitychange', elements.find(a => !a.paused) || elements[0]); flush()
    }
    const pagehide = () => { traceMedia('pagehide'); flush() }
    const pageshow = () => traceMedia('pageshow')
    for (const audio of elements) for (const event of events) audio.addEventListener(event, log)
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('pagehide', pagehide)
    window.addEventListener('pageshow', pageshow)
    for (const [slot, audio] of elements.entries()) traceMedia('audio-snapshot', audio, { slot })
    return () => {
      for (const audio of elements) for (const event of events) audio.removeEventListener(event, log)
      document.removeEventListener('visibilitychange', visibility)
      window.removeEventListener('pagehide', pagehide)
      window.removeEventListener('pageshow', pageshow)
      flush()
    }
  }, [capture, audioRef, secondaryAudioRef])
  return null
}

function DiagnosticsCard() {
  const capture = useSyncExternalStore(subscribe, getEnabled)
  const [report, setReport] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const textRef = useRef<HTMLTextAreaElement | null>(null)
  function view() { setReport(getMediaDiagnosticsReport()); setStatus('') }
  async function copy() {
    const text = getMediaDiagnosticsReport()
    setReport(text)
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(text)
      setStatus('Media log copied.')
    } catch { setStatus('Clipboard unavailable. Select and copy the log below manually.') }
  }
  return <div className="settings-card">
    <div className="settings-card-heading-row"><div><h3>Media Playback Diagnostics</h3><p>Record lock-screen controls, audio events and playback timings on this device.</p></div></div>
    <div className="settings-control-row"><div><strong>Diagnostic logging</strong><span>{capture ? 'Recording is enabled.' : 'Recording is disabled.'} Changes apply immediately; no Save required.</span></div>
      <button type="button" className={`settings-toggle ${capture ? 'on' : ''}`} role="switch" aria-label="Enable media playback diagnostics" aria-checked={capture} onClick={() => { setMediaDiagnosticsEnabled(!capture); setStatus(!capture ? 'Recording enabled.' : 'Recording stopped. Existing log retained.') }}><span className="settings-toggle-thumb" /></button>
    </div>
    <p className="settings-note">Enable logging, reproduce the problem while your phone is locked, then return here to copy the report. The last {MAX} events are stored locally and are not uploaded. Disable logging when finished.</p>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      <button type="button" onClick={view}>{report === null ? 'View log' : 'Refresh log'}</button>
      <button type="button" onClick={() => void copy()}>Copy log</button>
      <button type="button" onClick={() => { clearMediaDiagnostics(); setReport(null); setStatus('Media log cleared.') }}>Clear log</button>
      {report !== null && <button type="button" onClick={() => { setReport(null); setStatus('') }}>Hide log</button>}
    </div>
    {status && <p className="settings-note" role="status">{status}</p>}
    {report !== null && <><textarea ref={textRef} readOnly aria-label="Media diagnostics log" value={report} spellCheck={false} style={{ width: '100%', boxSizing: 'border-box', minHeight: 200, fontFamily: 'monospace', fontSize: 13 }} /><div><button type="button" onClick={() => { const field = textRef.current; if (field) { field.focus(); field.select(); field.setSelectionRange(0, field.value.length) } }}>Select log text</button></div></>}
  </div>
}

// The diagnostic controller is mounted in the persistent layout. Attach its card only
// while the existing Personal Settings / Advanced section is displayed. No overlay.
export function MediaDiagnosticsSettings() {
  const [host, setHost] = useState<HTMLDivElement | null>(null)
  useEffect(() => {
    const root = document.querySelector('.app-main-area')
    if (!root) return
    const mount = document.createElement('div')
    mount.style.display = 'contents'
    const sync = () => {
      const advanced = root.querySelector('.settings-page-user .settings-section-content > .settings-css-card')
      if (advanced?.parentElement) {
        if (mount.parentElement !== advanced.parentElement || mount.nextElementSibling !== advanced) advanced.before(mount)
        setHost(current => current === mount ? current : mount)
      } else {
        mount.remove()
        setHost(current => current === null ? current : null)
      }
    }
    const observer = new MutationObserver(sync)
    observer.observe(root, { childList: true, subtree: true })
    sync()
    return () => { observer.disconnect(); mount.remove() }
  }, [])
  return host ? createPortal(<DiagnosticsCard />, host) : null
}
