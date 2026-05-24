import { useState } from 'react'
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

export function PlaybackBar({ player, audioIntent, run, setPlayer, setError }: Props) {
  const [localPlaying, setLocalPlaying] = useState(false)
  const now = player?.now_playing
  const hasTrack = Boolean(now)

  return (
    <footer className="playback-bar">
      <div className="now-playing">
        <Artwork src={now?.art_url} alt={now?.title ?? 'No track'} />
        <div>
          <div className="eyebrow">Now Playing</div>
          <div className="title">{now?.title ?? 'Nothing selected'}</div>
          <div className="muted">{now ? `${now.artist}${now.album ? ` • ${now.album}` : ''}` : 'Search, queue, or start a station'}</div>
        </div>
      </div>

      <div className="transport-stack">
        <div className="transport">
          <button onClick={() => run(api.previous, localPlaying ? 'play' : 'pause')} disabled={!player}>Prev</button>
          {localPlaying ? (
            <button className="primary" onClick={() => run(api.pause, 'pause')} disabled={!hasTrack}>Pause</button>
          ) : (
            <button className="primary" onClick={() => run(api.resume, 'play')} disabled={!hasTrack}>Play</button>
          )}
          <button onClick={() => run(api.next, localPlaying ? 'play' : 'pause')} disabled={!player}>Next</button>
        </div>
        <label className="autoplay-toggle">
          <input
            type="checkbox"
            checked={player?.autoplay_enabled ?? false}
            onChange={(event) => run(() => api.setAutoplay(event.target.checked))}
          />
          Continuous queue
        </label>
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
