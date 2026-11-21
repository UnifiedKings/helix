import type { ReactNode, RefObject } from 'react'
import { Artwork } from './Artwork'
import { ArtistLink } from './ArtistLink'
import { AlbumLink } from './AlbumLink'

type PlaybarStyle = 'helix' | 'ytmusic' | 'spotify' | 'pandora'
type SkipDirection = 'previous' | 'next'

type Props = {
  artwork?: string
  title?: string
  artist?: string
  album?: string
  source?: string
  hasTrack: boolean
  playing: boolean
  repeatTrack: boolean
  onPrevious: () => void
  onNext: () => void
  onTogglePlayback: () => void
  onToggleRepeat: () => void
  audioControls: ReactNode
  ratingControls?: ReactNode
  playbarStyle?: PlaybarStyle
  skipDirection?: SkipDirection | null
  barRef?: RefObject<HTMLElement | null>
  seekHitbox?: ReactNode
  className?: string
  anonymous?: boolean
}

function PreviousIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5v14" /><path d="M19 6.5 9 12l10 5.5V6.5Z" /></svg>
}
function NextIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 5v14" /><path d="M5 6.5 15 12 5 17.5V6.5Z" /></svg>
}
function PlayIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5-11-6.5Z" /></svg>
}
function PauseIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14" /><path d="M16 5v14" /></svg>
}
function RepeatIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 2l4 4-4 4" /><path d="M3 11V9a3 3 0 0 1 3-3h15" /><path d="M7 22l-4-4 4-4" /><path d="M21 13v2a3 3 0 0 1-3 3H3" /></svg>
}

export function PlaybackBarSurface({
  artwork, title, artist, album, source, hasTrack, playing, repeatTrack,
  onPrevious, onNext, onTogglePlayback, onToggleRepeat, audioControls,
  ratingControls, playbarStyle = 'helix', skipDirection, barRef,
  seekHitbox, className = '', anonymous = false,
}: Props) {
  return (
    <footer
      ref={barRef}
      className={`playback-bar playback-style-${playbarStyle} ${className}`.trim()}
      data-playbar-style={playbarStyle}
      data-skip-direction={skipDirection ?? undefined}
      aria-label="Music player"
    >
      <div className="now-playing">
        <Artwork src={artwork} alt={title ?? 'No track'} />
        <div className="now-playing-info">
          <div className="eyebrow">Now Playing</div>
          <div className="title">{title ?? 'Nothing selected'}</div>
          <div className="muted now-playing-meta">
            {hasTrack ? (
              anonymous ? <>{artist}{album ? ` • ${album}` : ''}</> : <>
                <ArtistLink artist={artist ?? ''} />
                {album ? <><span className="now-playing-meta-separator" aria-hidden="true">•</span><AlbumLink album={album} artist={artist ?? ''} source={source as 'subsonic' | 'youtube' | undefined} /></> : null}
              </>
            ) : 'Search, queue, or start a station'}
          </div>
        </div>
        {ratingControls ?? null}
      </div>
      <div className="transport-stack">
        <div className="transport">
          <button className="icon-button transport-side transport-previous" aria-label="Previous track" title="Previous" onClick={onPrevious} disabled={!hasTrack}>
            <PreviousIcon />
          </button>
          <button className="primary transport-main" aria-label={playing ? 'Pause' : 'Play'} title={playing ? 'Pause' : 'Play'} onClick={onTogglePlayback} disabled={!hasTrack}>
            {playing ? <PauseIcon /> : <PlayIcon />}
          </button>
          <button className="icon-button transport-side transport-next" aria-label="Next track" title="Next" onClick={onNext} disabled={!hasTrack}>
            <NextIcon />
          </button>
          <button className="icon-button transport-extra transport-repeat" type="button" title={repeatTrack ? 'Repeat track on' : 'Repeat track off'} aria-label={repeatTrack ? 'Disable track repeat' : 'Enable track repeat'} aria-pressed={repeatTrack} data-active={repeatTrack} onClick={onToggleRepeat} disabled={!hasTrack}>
            <RepeatIcon />
          </button>
        </div>
      </div>
      {audioControls}
      {seekHitbox}
      <div className="ytmusic-utility-repeat">
        <button className="icon-button transport-extra ytmusic-repeat-button" type="button" title={repeatTrack ? 'Repeat track on' : 'Repeat track off'} aria-label={repeatTrack ? 'Disable track repeat' : 'Enable track repeat'} aria-pressed={repeatTrack} data-active={repeatTrack}
          onPointerDown={event => event.stopPropagation()}
          onPointerUp={event => event.stopPropagation()}
          onMouseDown={event => event.stopPropagation()}
          onClick={event => { event.stopPropagation(); onToggleRepeat() }} disabled={!hasTrack}>
          <RepeatIcon />
        </button>
      </div>
    </footer>
  )
}
