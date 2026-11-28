import { useEffect, useRef } from 'react'
import type { QueueItem } from '../api/types'
import { traceMedia } from './mediaDiagnostics'

type Transport = 'play' | 'pause' | 'nexttrack' | 'previoustrack' | 'stop'
type Options = { enabled: boolean; track: QueueItem | null; audio: () => HTMLAudioElement | null; onTransport: (action: Transport) => void; onSeek: (time: number) => void }
const actions: MediaSessionAction[] = ['play','pause','nexttrack','previoustrack','stop','seekto','seekforward','seekbackward']
function artwork(value?: string) {
  try { const url = new URL(value || '/favicon.png', window.location.origin); if (['http:', 'https:'].includes(url.protocol)) return url.href } catch {}
  return new URL('/favicon.png', window.location.origin).href
}
export function publishPlaybackState(playing: boolean) { try { if ('mediaSession' in navigator) navigator.mediaSession.playbackState = playing ? 'playing' : 'paused' } catch {} }
export function publishPosition(audio: HTMLAudioElement | null) {
  try {
    if (!audio || !('mediaSession' in navigator) || !Number.isFinite(audio.duration) || audio.duration <= 0 || !Number.isFinite(audio.currentTime)) return
    navigator.mediaSession.setPositionState?.({duration: audio.duration, position: Math.max(0, Math.min(audio.duration, audio.currentTime)), playbackRate: audio.playbackRate > 0 ? audio.playbackRate : 1})
  } catch {}
}
/** Register once per player lifetime. Metadata and native position are independent. */
export function useMediaSession(options: Options) {
  const latest = useRef(options); latest.current = options
  const { enabled, track } = options
  useEffect(() => {
    if (!enabled || !('mediaSession' in navigator)) return
    const session = navigator.mediaSession
    const register = (action: MediaSessionAction, handler: MediaSessionActionHandler | null) => { try { session.setActionHandler(action, handler) } catch { traceMedia('session-handler-error', latest.current.audio(), { action }) } }
    const seek = (time: number) => { if (Number.isFinite(time)) latest.current.onSeek(time) }
    for (const action of ['play','pause','nexttrack','previoustrack','stop'] as const) register(action, () => {
      traceMedia('os-' + action, latest.current.audio()); latest.current.onTransport(action)
    })
    register('seekto', details => { if (typeof details.seekTime === 'number') seek(details.seekTime) })
    register('seekforward', details => seek((latest.current.audio()?.currentTime ?? 0) + (details.seekOffset ?? 10)))
    register('seekbackward', details => seek((latest.current.audio()?.currentTime ?? 0) - (details.seekOffset ?? 10)))
    traceMedia('session-register', latest.current.audio())
    return () => { traceMedia('session-unregister', latest.current.audio()); for (const action of actions) register(action, null); try { session.metadata = null; session.playbackState = 'none'; session.setPositionState?.() } catch {} }
  }, [enabled])
  const id = track?.id, title = track?.title, artist = track?.artist, album = track?.album, art = track?.art_url || track?.thumbnail_url || track?.thumbnail
  useEffect(() => {
    if (!enabled || !('mediaSession' in navigator)) return
    // Coalesce rapid changes, without unregistering system controls.
    const commit = () => {
      try {
        if (!id) { traceMedia('session-metadata-cleared', latest.current.audio()); navigator.mediaSession.metadata = null; navigator.mediaSession.playbackState = 'none'; navigator.mediaSession.setPositionState?.(); return }
        if (typeof MediaMetadata !== 'undefined') navigator.mediaSession.metadata = new MediaMetadata({ title: title || 'Unknown track', artist: artist || '', album: album || '', artwork: [{src: artwork(art)}] })
        traceMedia('session-metadata-updated', latest.current.audio())
      } catch {}
    }
    const timer = window.setTimeout(commit, 100)
    return () => window.clearTimeout(timer)
  }, [enabled, id, title, artist, album, art])
}
