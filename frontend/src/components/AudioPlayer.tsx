import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { AudioIntent, PlayerState } from '../api/types'
import type { AudioRunMode } from '../hooks/usePlayer'
import { useMediaSession, publishPlaybackState, publishPosition } from '../hooks/useMediaSession'
import { MediaDiagnostics, traceMedia } from '../hooks/mediaDiagnostics'

type Props = {
  player: PlayerState | null
  audioIntent: AudioIntent
  onStateChange: (player: PlayerState) => void
  repeatTrack?: boolean
  onLocalPlayingChange?: (playing: boolean) => void
  onError?: (message: string) => void
  run?: (action: () => Promise<PlayerState>, audioMode?: AudioRunMode) => Promise<PlayerState>
}

function streamUrl(queueItemId: string) {
  return `/api/stream/${encodeURIComponent(queueItemId)}`
}

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function positionKey(queueItemId: string) {
  return `helix.playback.position.${queueItemId}`
}

function readSavedPosition(queueItemId: string) {
  const saved = window.localStorage.getItem(positionKey(queueItemId))
  const parsed = saved === null ? Number.NaN : Number(saved)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

function savePosition(queueItemId: string, seconds: number) {
  if (!queueItemId || !Number.isFinite(seconds) || seconds < 0) return
  window.localStorage.setItem(positionKey(queueItemId), String(seconds))
}

function clearPosition(queueItemId: string) {
  if (!queueItemId) return
  window.localStorage.removeItem(positionKey(queueItemId))
}

export function AudioPlayer({ player, audioIntent, onStateChange, repeatTrack = false, onLocalPlayingChange, onError, run }: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const currentItemIdRef = useRef<string>('')
  const pendingRestoreRef = useRef(0)
  const lastIntentIdRef = useRef(0)
  const continueAfterEndedRef = useRef(false)
  const playAttemptRef = useRef(0)
  const retryCleanupRef = useRef<(() => void) | null>(null)
  const advanceTimerRef = useRef<number | null>(null)
  const latestIntentRef = useRef(audioIntent)
  latestIntentRef.current = audioIntent
  const [audioError, setAudioError] = useState('')
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(() => {
    const saved = window.localStorage.getItem('helix.volume')
    const parsed = saved === null ? Number.NaN : Number(saved)
    return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : 0.85
  })

  useEffect(() => {
    if (window.localStorage.getItem('helix.volume') !== null) return
    let cancelled = false
    void api.userSettings().then((prefs) => {
      if (!cancelled) setVolume(Math.max(0, Math.min(1, prefs.settings.playback_default_volume)))
    }).catch(() => undefined)
    return () => { cancelled = true }
  }, [])

  const now = player?.now_playing ?? null
  const nowId = now?.id ?? ''

  function cancelPlayAttempt() {
    playAttemptRef.current += 1
    retryCleanupRef.current?.()
    retryCleanupRef.current = null
  }

  // Keep main's immediate play() path. The existing readiness/timer fallback is
  // recovery only: never hold up a normal play or interrupt an in-flight play().
  function attemptPlayback(audio: HTMLAudioElement, itemId: string, advancing = false) {
    cancelPlayAttempt()
    const attemptId = playAttemptRef.current
    const current = () => attemptId === playAttemptRef.current && currentItemIdRef.current === itemId && audioRef.current === audio
    let retried = false
    let timer: number | null = null
    let waiting = false
    const cleanup = () => {
      if (timer !== null) window.clearTimeout(timer)
      timer = null
      audio.removeEventListener('canplay', retryReady)
      if (retryCleanupRef.current === cleanup) retryCleanupRef.current = null
    }
    const failure = (err: unknown) => {
      if (!current()) return
      cleanup()
      if (advancing) continueAfterEndedRef.current = false
      const name = err instanceof DOMException ? err.name : ''
      if (!advancing && name !== 'AbortError' && name !== 'NotAllowedError') {
        const message = err instanceof Error ? err.message : 'Browser blocked audio playback'
        setAudioError(message)
        onError?.(message)
      }
      onLocalPlayingChange?.(false)
      if (run) publishPlaybackState(false)
    }
    function retryReady() { if (waiting) retry('readiness') }
    function retry(reason: 'readiness' | 'timer') {
      if (!current() || retried || !waiting) return
      retried = true
      waiting = false
      cleanup()
      start(reason)
    }
    function start(reason: 'immediate' | 'readiness' | 'timer') {
      if (!current()) return
      traceMedia('engine-play-attempt', audio, { readiness: reason === 'readiness', fallback: reason === 'timer' })
      void audio.play().then(() => {
        if (!current()) return
        cleanup()
        if (advancing) continueAfterEndedRef.current = true
        onLocalPlayingChange?.(true)
        if (run) { publishPlaybackState(true); publishPosition(audio) }
      }).catch((err: unknown) => {
        if (!current()) return
        const name = err instanceof DOMException ? err.name : ''
        if (!retried && (name === 'AbortError' || name === 'NotAllowedError')) {
          waiting = true
          retryCleanupRef.current = cleanup
          audio.addEventListener('canplay', retryReady)
          timer = window.setTimeout(() => retry('timer'), 250)
        } else failure(err)
      })
    }
    start('immediate')
  }

  useEffect(() => () => {
    cancelPlayAttempt()
    if (advanceTimerRef.current !== null) window.clearTimeout(advanceTimerRef.current)
  }, [])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    if (!nowId) {
      currentItemIdRef.current = ''
      cancelPlayAttempt()
      audio.pause()
      continueAfterEndedRef.current = false
      audio.removeAttribute('src')
      audio.load()
      setCurrentTime(0)
      setDuration(0)
      onLocalPlayingChange?.(false)
      return
    }

    if (currentItemIdRef.current !== nowId) {
      currentItemIdRef.current = nowId
      cancelPlayAttempt()
      audio.pause()
      audio.src = streamUrl(nowId)
      pendingRestoreRef.current = audioIntent.id !== lastIntentIdRef.current ? 0 : readSavedPosition(nowId)
      audio.load()
      setCurrentTime(pendingRestoreRef.current)
      setDuration(0)
      onLocalPlayingChange?.(false)
    }
  }, [audioIntent.id, nowId, onLocalPlayingChange])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    if (audioIntent.id === 0 || lastIntentIdRef.current === audioIntent.id) return
    lastIntentIdRef.current = audioIntent.id

    if (advanceTimerRef.current !== null) { window.clearTimeout(advanceTimerRef.current); advanceTimerRef.current = null }
    if (audioIntent.action === 'pause') {
      cancelPlayAttempt()
      audio.pause()
      continueAfterEndedRef.current = false
      onLocalPlayingChange?.(false)
      if (run) publishPlaybackState(false)
      return
    }

    if (audioIntent.action === 'play' && nowId) {
      if (currentItemIdRef.current !== nowId) {
        currentItemIdRef.current = nowId
        audio.src = streamUrl(nowId)
        pendingRestoreRef.current = 0
        audio.load()
      }

      setAudioError('')
      attemptPlayback(audio, nowId)
    }
  }, [audioIntent, nowId, onError, onLocalPlayingChange])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    audio.volume = volume
    window.localStorage.setItem('helix.volume', String(volume))
  }, [volume])

  async function handleEnded() {
    const endedItemId = currentItemIdRef.current
    const shouldContinue = continueAfterEndedRef.current
    const endedIntentId = latestIntentRef.current.id
    clearPosition(endedItemId)
    onLocalPlayingChange?.(false)

    try {
      setAudioError('')
      const next = await api.ended()
      if (!audioRef.current || latestIntentRef.current.id !== endedIntentId || currentItemIdRef.current !== endedItemId) return
      onStateChange(next)

      const shouldAdvanceAndPlay =
        shouldContinue &&
        Boolean(next.is_playing) &&
        Boolean(next.now_playing) &&
        next.now_playing?.id !== endedItemId

      if (shouldAdvanceAndPlay && next.now_playing) {
        // Preserve main's timer-based automatic advancement. Reuse the source
        // if the state effect has already selected the same track.
        advanceTimerRef.current = window.setTimeout(() => {
          advanceTimerRef.current = null
          const audio = audioRef.current
          if (!audio || !next.now_playing || !next.is_playing || latestIntentRef.current.id !== endedIntentId) return
          if (currentItemIdRef.current !== endedItemId && currentItemIdRef.current !== next.now_playing.id) return
          if (currentItemIdRef.current !== next.now_playing.id) {
            currentItemIdRef.current = next.now_playing.id
            audio.src = streamUrl(next.now_playing.id)
            audio.load()
          }
          pendingRestoreRef.current = 0
          attemptPlayback(audio, next.now_playing.id, true)
        }, 0)
      } else {
        continueAfterEndedRef.current = false
        const audio = audioRef.current
        if (audio) {
          audio.pause()
          if (!next.is_playing) {
            audio.removeAttribute('src')
            audio.load()
          }
        }
        onLocalPlayingChange?.(false)
        if (run) publishPlaybackState(false)
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not advance playback'
      setAudioError(message)
      onError?.(message)
    }
  }

  function handleError() {
    const audio = audioRef.current
    if (!audio || !currentItemIdRef.current || !audio.currentSrc) return
    cancelPlayAttempt()
    const message = audio.error ? `Audio playback error ${audio.error.code}` : 'Audio playback failed'
    setAudioError(message)
    onError?.(message)
    onLocalPlayingChange?.(false)
    if (run) publishPlaybackState(false)
  }

  function seek(seconds: number) {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(seconds)) return
    audio.currentTime = seconds
    setCurrentTime(seconds)
    savePosition(currentItemIdRef.current, seconds)
    if (run) publishPosition(audio)
  }

  // Media Session is an adapter to the same controls, not a playback engine.
  // Register handlers once and use the existing run() path for OS commands.
  useMediaSession({
    enabled: Boolean(run), track: now,
    audio: () => audioRef.current,
    onSeek: seconds => {
      const audio = audioRef.current
      if (audio && Number.isFinite(audio.duration) && audio.duration > 0) seek(Math.max(0, Math.min(audio.duration, seconds)))
    },
    onTransport: action => {
      if (!run || !nowId) return
      const cmd = action === 'pause' || action === 'stop' ? api.pause : action === 'play' ? api.resume : action === 'nexttrack' ? api.next : api.previous
      const mode = action === 'pause' || action === 'stop' ? 'pause' : action === 'play' || player?.is_playing || (audioRef.current && !audioRef.current.paused) ? 'play' : 'pause'
      void run(cmd, mode).catch(error => traceMedia('os-command-error', audioRef.current, { message: String(error) }))
    },
  })

  return (
    <div className="audio-player">
      {run ? <MediaDiagnostics audioRef={audioRef} /> : null}
      <audio
        ref={audioRef}
        preload="auto"
        loop={repeatTrack}
        onEnded={handleEnded}
        onError={handleError}
        onPause={(event) => {
          if (!event.currentTarget.ended) continueAfterEndedRef.current = false
          onLocalPlayingChange?.(false)
          if (run) publishPlaybackState(false)
        }}
        onPlay={() => {
          continueAfterEndedRef.current = true
          onLocalPlayingChange?.(true)
          if (run) publishPlaybackState(true)
        }}
        onLoadedMetadata={(event) => {
          const audio = event.currentTarget
          const mediaDuration = audio.duration || 0
          setDuration(mediaDuration)

          const savedPosition = pendingRestoreRef.current
          const safePosition = mediaDuration > 0 ? Math.min(savedPosition, Math.max(0, mediaDuration - 3)) : savedPosition
          if (safePosition > 0) {
            audio.currentTime = safePosition
            setCurrentTime(safePosition)
          }
          pendingRestoreRef.current = 0
          if (run) publishPosition(audio)
        }}
        onTimeUpdate={(event) => {
          const seconds = event.currentTarget.currentTime || 0
          setCurrentTime(seconds)
          savePosition(currentItemIdRef.current, seconds)
          if (run) publishPosition(event.currentTarget)
        }}
      />

      <div className="scrub-row">
        <span>{formatTime(currentTime)}</span>
        <input
          aria-label="Playback position"
          className="scrub-input"
          type="range"
          min="0"
          max={duration || 0}
          step="1"
          value={Math.min(currentTime, duration || currentTime)}
          disabled={!nowId || !duration}
          onChange={(event) => seek(Number(event.target.value))}
        />
        <span>{formatTime(duration)}</span>
      </div>

      <div className="volume-row">
        <span>Volume</span>
        <input
          aria-label="Volume"
          className="volume-input"
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={volume}
          onChange={(event) => setVolume(Number(event.target.value))}
        />
      </div>

      {audioError ? <div className="audio-error">{audioError}</div> : null}
    </div>
  )
}
