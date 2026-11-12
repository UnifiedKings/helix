import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useParams } from 'react-router-dom'
import '../styles/shares.css'

type SharedTrack = {
  id: string
  title: string
  artist: string
  album: string
  duration_ms: number
  playable: boolean
  art_url: string
}

type SharedMedia = {
  type: 'track' | 'album' | 'playlist'
  title: string
  artist: string
  art_url: string
  tracks: SharedTrack[]
  expires_at?: string | null
}

function timeLabel(seconds: number) {
  if (!Number.isFinite(seconds)) return '0:00'
  const value = Math.max(0, Math.floor(seconds))
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`
}

function totalDurationLabel(tracks: SharedTrack[]) {
  const total = tracks.reduce((sum, track) => sum + Math.max(0, Number(track.duration_ms || 0)), 0)
  if (!total) return ''
  const totalSeconds = Math.floor(total / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)

  if (hours) return `${hours} hr ${minutes ? `${minutes} min` : ''}`.trim()
  return `${Math.max(1, minutes)} min`
}

function PlayIcon({ playing }: { playing: boolean }) {
  return playing ? (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zM13 5h4v14h-4z" fill="currentColor" /></svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7Z" fill="currentColor" /></svg>
  )
}

function PreviousIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5v14M18 6l-9 6 9 6Z" fill="currentColor" /></svg>
}

function NextIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 5v14M6 6l9 6-9 6Z" fill="currentColor" /></svg>
}

function VolumeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 10v4h4l5 4V6l-5 4H4Z" fill="currentColor" />
      <path d="M16 9.2c1.2 1.6 1.2 4 0 5.6M18.5 7c2.3 2.8 2.3 7.2 0 10" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  )
}

function ShareIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 16V4m0 0-4 4m4-4 4 4M5 13v6h14v-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function EqualizerIcon() {
  return (
    <span className="public-share-equalizer" aria-hidden="true">
      <i /><i /><i />
    </span>
  )
}

export function PublicSharePage() {
  const { token = '' } = useParams()
  const audio = useRef<HTMLAudioElement>(null)
  const playIntent = useRef(false)
  const [data, setData] = useState<SharedMedia | null>(null)
  const [error, setError] = useState('')
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(.8)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    fetch(`/api/public/shares/${encodeURIComponent(token)}`, { cache: 'no-store' })
      .then(async response => {
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Share unavailable')
        return response.json()
      })
      .then((payload: SharedMedia) => {
        setData(payload)
        const firstPlayable = payload.tracks.findIndex(track => track.playable)
        setIndex(Math.max(0, firstPlayable))
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Share unavailable'))
  }, [token])

  const track = data?.tracks[index]
  const itemCount = data?.tracks.length ?? 0
  const durationSummary = useMemo(() => data ? totalDurationLabel(data.tracks) : '', [data])

  useEffect(() => {
    const element = audio.current
    if (!element || !track?.playable) return

    element.src = `/api/public/shares/${encodeURIComponent(token)}/stream/${encodeURIComponent(track.id)}`
    element.load()
    element.volume = volume
    setCurrent(0)
    setDuration(0)

    if (playIntent.current) {
      void element.play()
        .then(() => setPlaying(true))
        .catch(() => {
          playIntent.current = false
          setPlaying(false)
        })
    }
  }, [index, track?.id, token])

  function select(nextIndex: number) {
    if (data?.tracks[nextIndex]?.playable) setIndex(nextIndex)
  }

  function toggle() {
    const element = audio.current
    if (!element || !track?.playable) return

    if (element.paused) {
      playIntent.current = true
      void element.play()
        .then(() => setPlaying(true))
        .catch(() => {
          playIntent.current = false
          setPlaying(false)
        })
    } else {
      playIntent.current = false
      element.pause()
      setPlaying(false)
    }
  }

  function next() {
    if (!data) return
    for (let step = 1; step <= data.tracks.length; step++) {
      const nextIndex = (index + step) % data.tracks.length
      if (data.tracks[nextIndex].playable) {
        setIndex(nextIndex)
        return
      }
    }
  }

  function prev() {
    if (!data) return
    for (let step = 1; step <= data.tracks.length; step++) {
      const nextIndex = (index - step + data.tracks.length) % data.tracks.length
      if (data.tracks[nextIndex].playable) {
        setIndex(nextIndex)
        return
      }
    }
  }

  async function sharePage() {
    const url = window.location.href
    try {
      if (navigator.share) {
        await navigator.share({ title: data?.title || 'Helix share', url })
        return
      }
      await navigator.clipboard.writeText(url)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // Native share sheets can be dismissed; no error UI is necessary.
    }
  }

  if (error) {
    return (
      <main className="public-share-shell">
        <section className="public-share-error">
          <img src="/helix-logo.png" alt="Helix" />
          <h1>Share unavailable</h1>
          <p>{error}</p>
        </section>
      </main>
    )
  }

  if (!data) return <main className="public-share-shell"><p className="public-share-loading">Loading shared music…</p></main>

  const heroArt = data.art_url || track?.art_url || ''
  const playerArt = track?.art_url || data.art_url || ''
  const typeLabel = data.type === 'playlist' ? 'Shared playlist' : data.type === 'album' ? 'Shared album' : 'Shared track'
  const countLabel = data.type === 'track'
    ? ''
    : `${itemCount} ${itemCount === 1 ? 'track' : 'tracks'}`

  const accentStyle = heroArt ? ({ '--public-share-art': `url("${heroArt}")` } as CSSProperties) : undefined

  return (
    <main className="public-share-shell" style={accentStyle}>
      <div className="public-share-backdrop" aria-hidden="true">
        {heroArt ? <img src={heroArt} alt="" /> : null}
      </div>

      <section className="public-share-card">
        <header className="public-share-topbar">
          <div className="public-share-brand">
            <img src="/helix-logo.png" alt="" />
            <span>Helix</span>
          </div>
          <button className="public-share-page-share" type="button" onClick={() => void sharePage()} aria-label="Share this page">
            <ShareIcon />
            <span>{copied ? 'Copied' : 'Share'}</span>
          </button>
        </header>

        <section className="public-share-hero">
          {heroArt
            ? <img className="public-share-art" src={heroArt} alt={`${data.title} artwork`} />
            : <div className="public-share-art placeholder" aria-hidden="true" />
          }
          <div className="public-share-hero-copy">
            <span className="public-share-eyebrow">{typeLabel}</span>
            <h1>{data.title}</h1>
            {data.artist ? <p className="public-share-artist">{data.artist}</p> : null}
            <div className="public-share-summary">
              {countLabel ? <span>{countLabel}</span> : null}
              {countLabel && durationSummary ? <i aria-hidden="true">•</i> : null}
              {durationSummary ? <span>{durationSummary}</span> : null}
              {(countLabel || durationSummary) ? <i aria-hidden="true">•</i> : null}
              <span>Shared on Helix</span>
            </div>
          </div>
        </section>

        <audio
          ref={audio}
          onPlay={() => { playIntent.current = true; setPlaying(true) }}
          onPause={() => setPlaying(false)}
          onTimeUpdate={event => setCurrent(event.currentTarget.currentTime)}
          onLoadedMetadata={event => setDuration(event.currentTarget.duration || 0)}
          onEnded={() => { playIntent.current = true; next() }}
        />

        <section className="public-share-player" aria-label="Player">
          <div className="public-share-player-row">
            <div className="public-share-now">
              {playerArt
                ? <img src={playerArt} alt="" />
                : <div className="public-share-now-placeholder" aria-hidden="true" />
              }
              <div>
                <strong>{track?.title || data.title}</strong>
                <span>{track?.artist || data.artist}</span>
              </div>
            </div>

            <div className="public-share-player-center">
              <div className="public-share-controls">
                <button type="button" onClick={prev} aria-label="Previous track"><PreviousIcon /></button>
                <button className="share-play" type="button" onClick={toggle} disabled={!track?.playable} aria-label={playing ? 'Pause' : 'Play'}>
                  <PlayIcon playing={playing} />
                </button>
                <button type="button" onClick={next} aria-label="Next track"><NextIcon /></button>
              </div>

              <div className="public-share-timeline">
                <span>{timeLabel(current)}</span>
                <input
                  aria-label="Seek"
                  type="range"
                  className="public-share-range"
                  min={0}
                  max={duration || 0}
                  step="0.1"
                  value={Math.min(current, duration || 0)}
                  style={{ '--share-progress': `${duration > 0 ? Math.min(100, (current / duration) * 100) : 0}%` } as CSSProperties}
                  onChange={event => {
                    const value = Number(event.target.value)
                    if (audio.current) audio.current.currentTime = value
                    setCurrent(value)
                  }}
                />
                <span>{timeLabel(duration)}</span>
              </div>
            </div>

            <label className="public-share-volume">
              <VolumeIcon />
              <input
                aria-label="Volume"
                type="range"
                className="public-share-range"
                min={0}
                max={1}
                step="0.01"
                value={volume}
                style={{ '--share-progress': `${volume * 100}%` } as CSSProperties}
                onChange={event => {
                  const value = Number(event.target.value)
                  setVolume(value)
                  if (audio.current) audio.current.volume = value
                }}
              />
            </label>
          </div>
        </section>

        {data.tracks.length > 1 ? (
          <section className="public-share-track-section" aria-label="Tracks">
            <div className="public-share-track-heading" aria-hidden="true">
              <span>#</span>
              <span>Title</span>
              <span>Artist</span>
              <span>Duration</span>
            </div>

            <div className="public-share-tracklist">
              {data.tracks.map((item, itemIndex) => (
                <button
                  key={`${item.id}-${itemIndex}`}
                  type="button"
                  className={itemIndex === index ? 'active' : ''}
                  disabled={!item.playable}
                  onClick={() => select(itemIndex)}
                >
                  <span className="public-share-track-number">
                    {itemIndex === index && playing ? <EqualizerIcon /> : itemIndex + 1}
                  </span>
                  <span className="public-share-track-title">
                    {item.art_url ? <img src={item.art_url} alt="" /> : <span className="public-share-track-art-placeholder" />}
                    <span>
                      <strong>{item.title}</strong>
                      {!item.playable ? <small>Unavailable</small> : null}
                    </span>
                  </span>
                  <span className="public-share-track-artist">{item.artist}</span>
                  <span className="public-share-track-duration">{timeLabel((item.duration_ms || 0) / 1000)}</span>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        <footer>Read-only share{data.type === 'playlist' ? ' · Playlist changes appear here automatically' : ''}</footer>
      </section>
    </main>
  )
}
