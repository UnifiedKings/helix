import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { AudioIntent, PlayerState } from '../api/types'
import type { AudioRunMode } from '../hooks/usePlayer'
import { Artwork } from './Artwork'
import { AudioPlayer } from './AudioPlayer'

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


function IconShuffle() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 3h5v5" /><path d="M4 7h4c2.2 0 3.5 1.2 5 3l1 1.2" /><path d="M21 3l-6.8 6.8" /><path d="M16 21h5v-5" /><path d="M4 17h4c2.2 0 3.5-1.2 5-3l1-1.2" /><path d="M21 21l-6.8-6.8" /></svg>
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

function IconVolume() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10v4h4l5 4.5v-13L8 10H4Z" /><path d="M17 9a4 4 0 0 1 0 6" /></svg>
  )
}

export function PlaybackBar({ player, audioIntent, run, setPlayer, setError }: Props) {
  const [localPlaying, setLocalPlaying] = useState(false)
  const [liked, setLiked] = useState(false)
  const [disliked, setDisliked] = useState(false)
  const [ratingBusy, setRatingBusy] = useState(false)
  const now = player?.now_playing
  const hasTrack = Boolean(now)
  const shouldKeepPlaying = Boolean(player?.is_playing || localPlaying)
  const trackIdentity = `${now?.subsonic_song_id ?? ''}|${now?.yt_video_id ?? ''}|${now?.id ?? ''}`

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
    <footer className="playback-bar">
      <div className="now-playing">
        <Artwork src={now?.art_url} alt={now?.title ?? 'No track'} />
        <div className="now-playing-info">
          <div className="eyebrow">Now Playing</div>
          <div className="title">{now?.title ?? 'Nothing selected'}</div>
          <div className="muted">{now ? `${now.artist}${now.album ? ` • ${now.album}` : ''}` : 'Search, queue, or start a station'}</div>
        </div>
        <div className="rating-controls" aria-label="Track rating controls">
          <button className="icon-button rating-button" aria-label="Dislike current track" title="Dislike" onClick={toggleDislike} disabled={!hasTrack || ratingBusy} data-active={disliked}>
            <IconThumbDown />
          </button>
          <button className="icon-button rating-button" aria-label="Like current track" title="Like" onClick={toggleLike} disabled={!hasTrack || ratingBusy} data-active={liked}>
            <IconThumbUp />
          </button>
        </div>
      </div>

      <div className="transport-stack">
        <div className="transport">
          <button className="icon-button transport-extra" type="button" title="Shuffle placeholder" aria-label="Shuffle placeholder" disabled>
            <IconShuffle />
          </button>
          <button className="icon-button transport-side" aria-label="Previous track" title="Previous" onClick={() => run(api.previous, shouldKeepPlaying ? 'play' : 'pause')} disabled={!player}>
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
          <button className="icon-button transport-side" aria-label="Next track" title="Next" onClick={() => run(api.next, shouldKeepPlaying ? 'play' : 'pause')} disabled={!player}>
            <IconNext />
          </button>
          <button className="icon-button transport-extra" type="button" title="Repeat placeholder" aria-label="Repeat placeholder" disabled>
            <IconRepeat />
          </button>
        </div>
      </div>

      <AudioPlayer
        player={player}
        audioIntent={audioIntent}
        onStateChange={setPlayer}
        onLocalPlayingChange={setLocalPlaying}
        onError={setError}
      />
    </footer>
  )
}
