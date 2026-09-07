import { useEffect, useMemo, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import { subscribePersistentCache } from '../api/persistentCache'
import type { HomeActivityItem, HomeSummary } from '../api/types'
import { Artwork } from '../components/Artwork'
import { ArtistLink } from '../components/ArtistLink'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

function formatDuration(ms?: number) {
  const totalSeconds = Math.max(0, Math.floor((ms ?? 0) / 1000))
  if (!totalSeconds) return '0:00'
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

function relativeTime(value?: string) {
  if (!value) return ''
  const then = new Date(value).getTime()
  if (!Number.isFinite(then)) return ''
  const deltaSeconds = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (deltaSeconds < 60) return 'just now'
  const minutes = Math.floor(deltaSeconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function ActivityList({ items }: { items: HomeActivityItem[] }) {
  return (
    <section className="home-activity-section">
      <div className="home-section-heading">
        <h2>Recent Activity</h2>
        <Link to="/history">View History <span aria-hidden="true">›</span></Link>
      </div>
      <div className="home-activity-list">
        {items.length ? items.slice(0, 6).map((item) => (
          <div className="home-activity-item" key={item.id}>
            {item.art_url ? (
              <Artwork src={item.art_url} alt={item.title} size="sm" />
            ) : (
              <span className="home-activity-icon" aria-hidden="true">{item.icon || '♪'}</span>
            )}
            <div className="home-activity-copy">
              <strong>{item.title}</strong>
              <span className="muted">{item.detail}</span>
            </div>
            <time>{relativeTime(item.created_at)}</time>
          </div>
        )) : (
          <p className="muted home-empty-copy">No recent activity yet.</p>
        )}
      </div>
    </section>
  )
}

export function HomePage() {
  const player = useOutletContext<PlayerContext>()
  const [summary, setSummary] = useState<HomeSummary | null>(null)
  const [error, setError] = useState('')
  const [subsonicState, setSubsonicState] = useState<'unknown' | 'available' | 'missing' | 'queued'>('unknown')
  const current = player.player?.now_playing ?? null
  const queue = player.player?.queue ?? []
  const station = player.player?.active_station ?? null
  const activeStation = Boolean(player.player?.active_station_id && station)

  useEffect(() => {
    let cancelled = false

    const loadSummary = () => {
      api.homeSummary()
        .then((payload) => {
          if (cancelled) return
          setSummary(payload)
          setError('')
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load home summary')
        })
    }

    loadSummary()

    // Unlike the other cached pages, Home contains live playback-dependent UI.
    // Applying fresh recent activity in-place prevents an SWR cache update from
    // remounting Home and resetting transient state such as Subsonic resolution.
    const unsubscribe = subscribePersistentCache(['home:summary'], () => {
      loadSummary()
    })

    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    if (!current) {
      setSubsonicState('unknown')
      return () => { cancelled = true }
    }
    if (current.subsonic_song_id || current.source?.toLowerCase() === 'subsonic') {
      setSubsonicState('available')
      return () => { cancelled = true }
    }

    setSubsonicState('unknown')
    api.resolveSubsonicSongs([{
      key: current.id || 'now-playing',
      title: current.title,
      artist: current.artist,
      album: current.album,
      duration_ms: current.duration_ms,
      yt_video_id: current.yt_video_id,
    }])
      .then((payload) => {
        if (cancelled) return
        const match = payload.songs[current.id || 'now-playing']
        setSubsonicState(match?.available ? 'available' : 'missing')
      })
      .catch(() => { if (!cancelled) setSubsonicState('missing') })

    return () => { cancelled = true }
  }, [current?.id, current?.title, current?.artist, current?.album, current?.duration_ms, current?.subsonic_song_id, current?.source, current?.yt_video_id])

  async function addCurrentToSubsonic() {
    if (!current || subsonicState !== 'missing') return
    try {
      setError('')
      await api.addSongToSubsonic(current)
      setSubsonicState('queued')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add track to Subsonic')
    }
  }

  const session = useMemo(() => {
    if (!current) {
      return {
        label: 'Current Session',
        title: 'Nothing Playing',
        subtitle: 'Start with Search, a Station, or a Lobby.',
        icon: '♪',
      }
    }
    return {
      label: 'Now Playing',
      title: current.title,
      subtitle: current.artist,
      icon: '♪',
    }
  }, [current])

  const activity = summary?.recent_activity ?? []

  return (
    <div className="home-page home-page-refined">
      {error ? <div className="error-banner">{error}</div> : null}

      <section className={`home-session-card ${current ? 'active' : 'idle'}`}>
        {current?.art_url ? <div className="home-session-backdrop" style={{ backgroundImage: `url(${current.art_url})` }} /> : null}
        {current ? (
          <Link className="home-big-picture-action" to="/big-picture" aria-label="Open Big Picture" title="Big Picture">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5" />
            </svg>
          </Link>
        ) : null}
        <div className="home-session-art">
          {current ? <Artwork src={current.art_url} alt={current.title} size="lg" /> : <span aria-hidden="true">{session.icon}</span>}
        </div>
        <div className="home-session-copy">
          <span className="eyebrow">{session.label}</span>
          <div className="home-session-title-row">
            <h1 className={session.title.length > 70 ? 'is-very-long' : session.title.length > 38 ? 'is-long' : undefined}>
              {session.title}
            </h1>
          </div>
          {current ? (
            <p className="muted home-session-artist-row">
              <ArtistLink artist={session.subtitle} className="home-session-artist-link" />
            </p>
          ) : <p className="muted">{session.subtitle}</p>}
          {activeStation && station ? <p className="home-session-station">From station: <strong>{station.name}</strong></p> : null}
          <div className="home-session-actions">
            {activeStation && player.player?.active_station_id ? <Link className="button-link primary" to={`/stations?edit=${encodeURIComponent(player.player.active_station_id)}`}>Edit Station</Link> : null}
            {current ? <Link className="button-link" to="/stations">Change Station</Link> : <Link className="button-link primary" to="/search">Search Music</Link>}
            {current && subsonicState === 'missing' ? <button type="button" className="button-link home-subsonic-action" onClick={() => void addCurrentToSubsonic()}>＋ Add to Subsonic</button> : null}
            {current && subsonicState === 'available' ? <span className="home-subsonic-state" title="This track is already in Subsonic" aria-label="In Subsonic"><span aria-hidden="true">✓</span> In Subsonic</span> : null}
            {current && subsonicState === 'queued' ? <span className="home-subsonic-state queued"><span aria-hidden="true">↧</span> Queued for Subsonic</span> : null}
          </div>
          <div className="home-session-meta">
            <span className={player.player?.is_playing ? 'is-playing' : ''}>{player.player?.is_playing ? 'Playing' : current ? 'Paused' : 'Ready'}</span>
            <span>{queue.length} in queue</span>
            {current ? <span>{formatDuration(current.duration_ms)}</span> : null}
          </div>
        </div>
      </section>

      <nav className="home-quick-actions" aria-label="Quick actions">
        <Link className="home-quick-action" to="/search">
          <span className="home-quick-icon" aria-hidden="true">⌕</span>
          <span><strong>Search Music</strong><small>Find songs, albums, and artists</small></span>
        </Link>
        <Link className="home-quick-action" to="/stations">
          <span className="home-quick-icon" aria-hidden="true">◉</span>
          <span><strong>Start Station</strong><small>Create a station and let Helix build the vibe</small></span>
        </Link>
        <Link className="home-quick-action" to="/playlists">
          <span className="home-quick-icon" aria-hidden="true">♫</span>
          <span><strong>Playlists</strong><small>Play or manage saved playlists</small></span>
        </Link>
        <Link className="home-quick-action" to="/lobbies">
          <span className="home-quick-icon" aria-hidden="true">◎</span>
          <span><strong>Lobbies</strong><small>Listen together with friends</small></span>
        </Link>
      </nav>

      <ActivityList items={activity} />
    </div>
  )
}
