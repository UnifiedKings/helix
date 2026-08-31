import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { AudioIntent, PlayerState, UserSettings, UserSettingsPayload } from '../api/types'
import type { AudioRunMode } from '../hooks/usePlayer'
import { Artwork } from './Artwork'
import { ArtistLink } from './ArtistLink'
import { AlbumLink } from './AlbumLink'
import { AudioPlayer } from './AudioPlayer'

type PlaybarStyle = 'helix' | 'ytmusic' | 'spotify' | 'pandora'

type UserSettingsWithPlaybar = UserSettings & { playback_bar_style?: PlaybarStyle }

type Props = {
  player: PlayerState | null
  audioIntent: AudioIntent
  run: (action: () => Promise<PlayerState>, audioMode?: AudioRunMode) => Promise<PlayerState>
  setPlayer: (player: PlayerState) => void
  setError?: (message: string) => void
}

function IconThumbDown() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h9.2c1.1 0 2 .72 2.3 1.78l1.2 4.3c.12.42.18.85.18 1.28V12c0 1.1-.9 2-2 2h-4.6l.74 3.5c.13.62-.06 1.27-.51 1.72L12.4 20.33 6.9 14.8V5.1C6.9 3.94 5.96 3 4.8 3H4v11h2.9" /></svg>
  )
}

function IconThumbUp() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><g transform="translate(24 0) scale(-1 1)"><path d="M17 21H7.8c-1.1 0-2-.72-2.3-1.78l-1.2-4.3a4.7 4.7 0 0 1-.18-1.28V12c0-1.1.9-2 2-2h4.6l-.74-3.5c-.13-.62.06-1.27.51-1.72L11.6 3.67l5.5 5.53v9.7c0 1.16.94 2.1 2.1 2.1h.8V10h-2.9" /></g></svg>
  )
}

function IconRepeat() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 2l4 4-4 4" /><path d="M3 11V9a3 3 0 0 1 3-3h15" /><path d="M7 22l-4-4 4-4" /><path d="M21 13v2a3 3 0 0 1-3 3H3" /></svg>
  )
}

function IconPrevious() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5v14" /><path d="M19 6.5 9 12l10 5.5V6.5Z" /></svg>
  )
}

function IconNext() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 5v14" /><path d="M5 6.5 15 12 5 17.5V6.5Z" /></svg>
  )
}

function IconPlay() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5-11-6.5Z" /></svg>
  )
}

function IconPause() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14" /><path d="M16 5v14" /></svg>
  )
}

function IconLyrics() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h10" /><path d="M5 9h10" /><path d="M5 13h7" /><path d="M17 12v6.5a2.5 2.5 0 1 1-2-2.45V8l4-1" /></svg>
  )
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
  const now = player?.now_playing
  const hasTrack = Boolean(now)
  const shouldKeepPlaying = Boolean(player?.is_playing || localPlaying)
  const trackIdentity = `${now?.subsonic_song_id ?? ''}|${now?.yt_video_id ?? ''}|${now?.id ?? ''}`

  useEffect(() => {
    let cancelled = false

    void api.userSettings().then((payload) => {
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
        const [likeState, dislikeState] = await Promise.all([
          api.isLiked(now),
          api.isDisliked(now),
        ])
        if (cancelled) return
        setLiked(Boolean(likeState.liked))
        setDisliked(Boolean(dislikeState.disliked))
      } catch {
        if (cancelled) return
        setLiked(false)
        setDisliked(false)
      }
    }

    void loadRatingState()
    return () => {
      cancelled = true
    }
  }, [trackIdentity])

  useEffect(() => {
    window.localStorage.setItem('helix.repeatTrack', repeatTrack ? '1' : '0')
  }, [repeatTrack])

  useEffect(() => {
    if (playbarStyle !== 'ytmusic') return

    const syncProgress = () => {
      const bar = barRef.current
      if (!bar) return
      const input = bar.querySelector<HTMLInputElement>('.scrub-input')
      const value = Number(input?.value ?? 0)
      const max = Number(input?.max ?? 0)
      const percent = max > 0 && Number.isFinite(value)
        ? Math.max(0, Math.min(100, (value / max) * 100))
        : 0
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
    const nextValue = ratio * max
    input.value = String(nextValue)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    bar.style.setProperty('--helix-scrub-progress', `${ratio * 100}%`)
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

  return (
    <footer ref={barRef} className={`playback-bar playback-style-${playbarStyle}`} data-playbar-style={playbarStyle}>
      <div className="now-playing">
        <Artwork src={now?.art_url} alt={now?.title ?? 'No track'} />
        <div className="now-playing-info">
          <div className="eyebrow">Now Playing</div>
          <div className="title">{now?.title ?? 'Nothing selected'}</div>
          <div className="muted now-playing-meta">{now ? <><ArtistLink artist={now.artist} />{now.album ? <><span className="now-playing-meta-separator" aria-hidden="true">•</span><AlbumLink album={now.album} artist={now.artist} source={now.source} /></> : null}</> : 'Search, queue, or start a station'}</div>
        </div>
        <div className="rating-controls" aria-label="Track controls">
          <button className="icon-button rating-button rating-dislike" aria-label="Dislike current track" title="Dislike" onClick={toggleDislike} disabled={!hasTrack || ratingBusy} data-active={disliked}>
            <IconThumbDown />
          </button>
          <button className="icon-button rating-button rating-like" aria-label="Like current track" title="Like" onClick={toggleLike} disabled={!hasTrack || ratingBusy} data-active={liked}>
            <IconThumbUp />
          </button>
          <button className="icon-button lyrics-launch" type="button" aria-label="Open lyrics" title="Lyrics" onClick={() => navigate('/big-picture?view=lyrics')} disabled={!hasTrack}>
            <IconLyrics />
          </button>
        </div>
      </div>

      <div className="transport-stack">
        <div className="transport">
          <button className="icon-button transport-side transport-previous" aria-label="Previous track" title="Previous" onClick={() => run(api.previous, shouldKeepPlaying ? 'play' : 'pause')} disabled={!player}>
            <IconPrevious />
          </button>
          {localPlaying ? (
            <button className="primary transport-main" aria-label="Pause" title="Pause" onClick={() => run(api.pause, 'pause')} disabled={!hasTrack}>
              <IconPause />
            </button>
          ) : (
            <button className="primary transport-main" aria-label="Play" title="Play" onClick={() => run(api.resume, 'play')} disabled={!hasTrack}>
              <IconPlay />
            </button>
          )}
          <button className="icon-button transport-side transport-next" aria-label="Next track" title="Next" onClick={() => run(api.next, shouldKeepPlaying ? 'play' : 'pause')} disabled={!player}>
            <IconNext />
          </button>
          <button
            className="icon-button transport-extra transport-repeat"
            type="button"
            title={repeatTrack ? 'Repeat track on' : 'Repeat track off'}
            aria-label={repeatTrack ? 'Disable track repeat' : 'Enable track repeat'}
            aria-pressed={repeatTrack}
            data-active={repeatTrack}
            onClick={() => setRepeatTrack((enabled) => !enabled)}
            disabled={!hasTrack}
          >
            <IconRepeat />
          </button>
        </div>
      </div>

      <AudioPlayer
        player={player}
        audioIntent={audioIntent}
        onStateChange={setPlayer}
        repeatTrack={repeatTrack}
        onLocalPlayingChange={setLocalPlaying}
        onError={setError}
      />
      <div
        className="ytmusic-seek-hitbox"
        aria-hidden="true"
        onPointerDown={(event) => {
          if (playbarStyle !== 'ytmusic') return
          event.currentTarget.setPointerCapture(event.pointerId)
          updateYtMusicSeek(event.clientX)
        }}
        onPointerMove={(event) => {
          if (playbarStyle !== 'ytmusic') return
          if (event.currentTarget.hasPointerCapture(event.pointerId)) updateYtMusicSeek(event.clientX)
        }}
        onPointerUp={(event) => {
          if (playbarStyle !== 'ytmusic') return
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            updateYtMusicSeek(event.clientX)
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
        }}
      />
      <div className="ytmusic-utility-repeat">
        <button
          className="icon-button transport-extra ytmusic-repeat-button"
          type="button"
          title={repeatTrack ? 'Repeat track on' : 'Repeat track off'}
          aria-label={repeatTrack ? 'Disable track repeat' : 'Enable track repeat'}
          aria-pressed={repeatTrack}
          data-active={repeatTrack}
          onPointerDown={(event) => event.stopPropagation()}
          onPointerUp={(event) => event.stopPropagation()}
          onMouseDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation()
            setRepeatTrack((enabled) => !enabled)
          }}
          disabled={!hasTrack}
        >
          <IconRepeat />
        </button>
      </div>
    </footer>
  )
}
