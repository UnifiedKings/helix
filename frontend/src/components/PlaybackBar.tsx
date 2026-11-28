import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { AudioIntent, PlayerState, UserSettings, UserSettingsPayload } from '../api/types'
import type { AudioRunMode } from '../hooks/usePlayer'
import { AudioPlayer } from './AudioPlayer'
import { PlaybackBarSurface } from './PlaybackBarSurface'

type PlaybarStyle = 'helix' | 'ytmusic' | 'spotify' | 'pandora'
type SkipDirection = 'previous' | 'next'
type UserSettingsWithPlaybar = UserSettings & { playback_bar_style?: PlaybarStyle }

type Props = {
  player: PlayerState | null
  audioIntent: AudioIntent
  run: (action: () => Promise<PlayerState>, audioMode?: AudioRunMode) => Promise<PlayerState>
  setPlayer: (player: PlayerState) => void
  setError?: (message: string) => void
}

function IconThumbDown() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h9.2c1.1 0 2 .72 2.3 1.78l1.2 4.3c.12.42.18.85.18 1.28V12c0 1.1-.9 2-2 2h-4.6l.74 3.5c.13.62-.06 1.27-.51 1.72L12.4 20.33 6.9 14.8V5.1C6.9 3.94 5.96 3 4.8 3H4v11h2.9" /></svg>
}
function IconThumbUp() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><g transform="translate(24 0) scale(-1 1)"><path d="M17 21H7.8c-1.1 0-2-.72-2.3-1.78l-1.2-4.3a4.7 4.7 0 0 1-.18-1.28V12c0-1.1.9-2 2-2h4.6l-.74-3.5c-.13-.62.06-1.27.51-1.72L11.6 3.67l5.5 5.53v9.7c0 1.16.94 2.1 2.1 2.1h.8V10h-2.9" /></g></svg>
}
function IconLyrics() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h10" /><path d="M5 9h10" /><path d="M5 13h7" /><path d="M17 12v6.5a2.5 2.5 0 1 1-2-2.45V8l4-1" /></svg>
}
function isPlaybarStyle(value: unknown): value is PlaybarStyle {
  return value === 'helix' || value === 'ytmusic' || value === 'spotify' || value === 'pandora'
}

export function PlaybackBar({ player, audioIntent, run, setPlayer, setError }: Props) {
  const navigate = useNavigate()
  const barRef = useRef<HTMLElement | null>(null)
  const [localPlaying, setLocalPlaying] = useState(false)
  const [liked, setLiked] = useState(false)
  const [disliked, setDisliked] = useState(false)
  const [ratingBusy, setRatingBusy] = useState(false)
  const [repeatTrack, setRepeatTrack] = useState(() => window.localStorage.getItem('helix.repeatTrack') === '1')
  const [playbarStyle, setPlaybarStyle] = useState<PlaybarStyle>('helix')
  const [skipDirection, setSkipDirection] = useState<SkipDirection | null>(null)
  const skipFeedbackTimerRef = useRef<number | null>(null)
  const now = player?.now_playing
  const hasTrack = Boolean(now)
  const shouldKeepPlaying = Boolean(player?.is_playing || localPlaying)
  const trackIdentity = `${now?.subsonic_song_id ?? ''}|${now?.yt_video_id ?? ''}|${now?.id ?? ''}`

  useEffect(() => {
    let cancelled = false
    void api.userSettings().then(payload => {
      const style = (payload.settings as UserSettingsWithPlaybar).playback_bar_style
      if (!cancelled && isPlaybarStyle(style)) setPlaybarStyle(style)
    }).catch(() => undefined)

    const listener = (event: Event) => {
      const payload = (event as CustomEvent<UserSettingsPayload>).detail
      const next = (payload?.settings as UserSettingsWithPlaybar | undefined)?.playback_bar_style
      if (isPlaybarStyle(next)) setPlaybarStyle(next)
    }
    window.addEventListener('helix-user-settings-updated', listener)
    return () => {
      cancelled = true
      window.removeEventListener('helix-user-settings-updated', listener)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    async function loadRatingState() {
      if (!now) {
        setLiked(false)
        setDisliked(false)
        return
      }
      try {
        const [likeState, dislikeState] = await Promise.all([api.isLiked(now), api.isDisliked(now)])
        if (cancelled) return
        setLiked(Boolean(likeState.liked))
        setDisliked(Boolean(dislikeState.disliked))
      } catch {
        if (!cancelled) {
          setLiked(false)
          setDisliked(false)
        }
      }
    }
    void loadRatingState()
    return () => { cancelled = true }
  }, [trackIdentity])

  useEffect(() => {
    window.localStorage.setItem('helix.repeatTrack', repeatTrack ? '1' : '0')
  }, [repeatTrack])

  useEffect(() => () => {
    if (skipFeedbackTimerRef.current !== null) window.clearTimeout(skipFeedbackTimerRef.current)
  }, [])

  useEffect(() => {
    if (playbarStyle !== 'ytmusic') return
    const syncProgress = () => {
      const bar = barRef.current
      if (!bar) return
      const input = bar.querySelector<HTMLInputElement>('.scrub-input')
      const value = Number(input?.value ?? 0)
      const max = Number(input?.max ?? 0)
      const percent = max > 0 && Number.isFinite(value)
        ? Math.max(0, Math.min(100, (value / max) * 100)) : 0
      bar.style.setProperty('--helix-scrub-progress', `${percent}%`)
    }
    syncProgress()
    const timer = window.setInterval(syncProgress, 100)
    return () => {
      window.clearInterval(timer)
      barRef.current?.style.removeProperty('--helix-scrub-progress')
    }
  }, [playbarStyle, trackIdentity])

  function updateYtMusicSeek(clientX: number) {
    const bar = barRef.current
    if (!bar || playbarStyle !== 'ytmusic') return
    const input = bar.querySelector<HTMLInputElement>('.scrub-input')
    const max = Number(input?.max ?? 0)
    if (!input || !Number.isFinite(max) || max <= 0) return
    const rect = bar.getBoundingClientRect()
    if (rect.width <= 0) return
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    input.value = String(ratio * max)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    bar.style.setProperty('--helix-scrub-progress', `${ratio * 100}%`)
  }

  function runDirectionalSkip(direction: SkipDirection, action: () => Promise<PlayerState>) {
    setSkipDirection(null)
    window.requestAnimationFrame(() => {
      setSkipDirection(direction)
      if (skipFeedbackTimerRef.current !== null) window.clearTimeout(skipFeedbackTimerRef.current)
      skipFeedbackTimerRef.current = window.setTimeout(() => {
        setSkipDirection(null)
        skipFeedbackTimerRef.current = null
      }, 230)
    })
    void run(action, shouldKeepPlaying ? 'play' : 'pause').catch(() => undefined)
  }

  async function toggleLike() {
    if (!now || ratingBusy) return
    setRatingBusy(true)
    try {
      const result = await api.toggleLike(now)
      setLiked(result.liked)
      if (result.liked) setDisliked(false)
    } catch (err) {
      setError?.(err instanceof Error ? err.message : 'Could not update liked state')
    } finally {
      setRatingBusy(false)
    }
  }

  async function toggleDislike() {
    if (!now || ratingBusy) return
    setRatingBusy(true)
    try {
      const result = await api.toggleDislike(now)
      setDisliked(result.disliked)
      if (result.disliked) setLiked(false)
    } catch (err) {
      setError?.(err instanceof Error ? err.message : 'Could not update disliked state')
    } finally {
      setRatingBusy(false)
    }
  }

  const ratingControls = (
    <div className="rating-controls" aria-label="Track controls">
      <button className="icon-button rating-button rating-dislike" aria-label="Dislike current track" title="Dislike" onClick={toggleDislike} disabled={!hasTrack || ratingBusy} data-active={disliked}><IconThumbDown /></button>
      <button className="icon-button rating-button rating-like" aria-label="Like current track" title="Like" onClick={toggleLike} disabled={!hasTrack || ratingBusy} data-active={liked}><IconThumbUp /></button>
      <button className="icon-button lyrics-launch" type="button" aria-label="Open lyrics" title="Lyrics" onClick={() => navigate('/big-picture?view=lyrics')} disabled={!hasTrack}><IconLyrics /></button>
    </div>
  )

  const seekHitbox = (
    <div className="ytmusic-seek-hitbox" aria-hidden="true"
      onPointerDown={event => {
        if (playbarStyle !== 'ytmusic') return
        event.currentTarget.setPointerCapture(event.pointerId)
        updateYtMusicSeek(event.clientX)
      }}
      onPointerMove={event => {
        if (playbarStyle !== 'ytmusic') return
        if (event.currentTarget.hasPointerCapture(event.pointerId)) updateYtMusicSeek(event.clientX)
      }}
      onPointerUp={event => {
        if (playbarStyle !== 'ytmusic') return
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          updateYtMusicSeek(event.clientX)
          event.currentTarget.releasePointerCapture(event.pointerId)
        }
      }}
    />
  )

  return (
    <PlaybackBarSurface
      barRef={barRef}
      artwork={now?.art_url}
      title={now?.title}
      artist={now?.artist}
      album={now?.album}
      source={now?.source}
      hasTrack={hasTrack}
      playing={localPlaying}
      repeatTrack={repeatTrack}
      onPrevious={() => runDirectionalSkip('previous', api.previous)}
      onNext={() => runDirectionalSkip('next', api.next)}
      onTogglePlayback={() => void run(localPlaying ? api.pause : api.resume, localPlaying ? 'pause' : 'play').catch(() => undefined)}
      onToggleRepeat={() => setRepeatTrack(value => !value)}
      ratingControls={ratingControls}
      playbarStyle={playbarStyle}
      skipDirection={skipDirection}
      seekHitbox={seekHitbox}
      audioControls={<AudioPlayer player={player} audioIntent={audioIntent} onStateChange={setPlayer} repeatTrack={repeatTrack} onLocalPlayingChange={setLocalPlaying} onError={setError} run={run} />}
    />
  )
}
