import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { AudioIntent, PlayerState } from '../api/types'

type Props = {
  player: PlayerState | null
  audioIntent: AudioIntent
  onStateChange: (player: PlayerState) => void
  onLocalPlayingChange?: (playing: boolean) => void
  onError?: (message: string) => void
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

export function AudioPlayer({ player, audioIntent, onStateChange, onLocalPlayingChange, onError }: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const currentItemIdRef = useRef<string>('')
  const pendingRestoreRef = useRef(0)
  const lastIntentIdRef = useRef(0)
  const continueAfterEndedRef = useRef(false)
  const playAttemptRef = useRef(0)
  const [audioError, setAudioError] = useState('')
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(() => {
    const saved = window.localStorage.getItem('helix.volume')
    const parsed = saved === null ? Number.NaN : Number(saved)
    return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : 0.85
  })

  const now = player?.now_playing ?? null
  const nowId = now?.id ?? ''

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    if (!nowId) {
      currentItemIdRef.current = ''
      playAttemptRef.current += 1
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
      playAttemptRef.current += 1
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

    if (audioIntent.action === 'pause') {
      audio.pause()
      continueAfterEndedRef.current = false
      onLocalPlayingChange?.(false)
      return
    }

    if (audioIntent.action === 'play' && nowId) {
      if (currentItemIdRef.current !== nowId) {
        currentItemIdRef.current = nowId
        audio.src = streamUrl(nowId)
        pendingRestoreRef.current = 0
        audio.load()
      }

      const attemptId = ++playAttemptRef.current
      setAudioError('')
      audio.play().then(() => {
        if (attemptId !== playAttemptRef.current || currentItemIdRef.current !== nowId) return
        onLocalPlayingChange?.(true)
      }).catch((err) => {
        if (attemptId !== playAttemptRef.current || currentItemIdRef.current !== nowId) return
        const name = err instanceof DOMException ? err.name : ''
        // Rapid next/previous clicks intentionally interrupt pending play() calls
        // by changing src/load(). Those should not leave the playbar stuck.
        if (name === 'AbortError' || name === 'NotAllowedError') {
          onLocalPlayingChange?.(false)
          return
        }
        const message = err instanceof Error ? err.message : 'Browser blocked audio playback'
        setAudioError(message)
        onError?.(message)
        onLocalPlayingChange?.(false)
      })
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
    clearPosition(endedItemId)
    onLocalPlayingChange?.(false)

    try {
      setAudioError('')
      const next = await api.ended()
      onStateChange(next)

      if (shouldContinue && next.now_playing) {
        window.setTimeout(() => {
          const audio = audioRef.current
          if (!audio || !next.now_playing) return
          currentItemIdRef.current = next.now_playing.id
          pendingRestoreRef.current = 0
          audio.src = streamUrl(next.now_playing.id)
          audio.load()
          const attemptId = ++playAttemptRef.current
          void audio.play().then(() => {
            if (attemptId !== playAttemptRef.current || currentItemIdRef.current !== next.now_playing?.id) return
            continueAfterEndedRef.current = true
            onLocalPlayingChange?.(true)
          }).catch(() => {
            if (attemptId !== playAttemptRef.current) return
            continueAfterEndedRef.current = false
            onLocalPlayingChange?.(false)
          })
        }, 0)
      } else {
        continueAfterEndedRef.current = false
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
    const message = audio.error ? `Audio playback error ${audio.error.code}` : 'Audio playback failed'
    setAudioError(message)
    onError?.(message)
    onLocalPlayingChange?.(false)
  }

  function seek(seconds: number) {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(seconds)) return
    audio.currentTime = seconds
    setCurrentTime(seconds)
    savePosition(currentItemIdRef.current, seconds)
  }

  return (
    <div className="audio-player">
      <audio
        ref={audioRef}
        preload="auto"
        onEnded={handleEnded}
        onError={handleError}
        onPause={(event) => {
          if (!event.currentTarget.ended) continueAfterEndedRef.current = false
          onLocalPlayingChange?.(false)
        }}
        onPlay={() => {
          continueAfterEndedRef.current = true
          onLocalPlayingChange?.(true)
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
        }}
        onTimeUpdate={(event) => {
          const seconds = event.currentTarget.currentTime || 0
          setCurrentTime(seconds)
          savePosition(currentItemIdRef.current, seconds)
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
