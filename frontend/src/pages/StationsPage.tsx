import { useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { Station } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

function IconPlay() {
  return <span aria-hidden="true">▶</span>
}

function StationStat({ icon, value, label }: { icon: string; value: string | number; label: string }) {
  return (
    <div className="station-stat-card">
      <span className="station-stat-icon" aria-hidden="true">{icon}</span>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  )
}

function seedLabel(station: Station) {
  const seed = station.seed_artist || station.seed_title || 'Unknown seed'
  if (!station.seed_type) return seed
  return `${station.seed_type === 'artist' ? 'Seed artist' : 'Seed'} • ${seed}`
}

export function StationsPage() {
  const player = useOutletContext<ReturnType<typeof usePlayer>>()
  const [stations, setStations] = useState<Station[]>([])
  const [error, setError] = useState('')

  async function load() {
    try {
      setStations(await api.stations())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load stations')
    }
  }

  useEffect(() => { void load() }, [])

  const recentlyUpdatedCount = useMemo(() => {
    return stations.filter((station) => station.updated_at || station.created_at).length || stations.length
  }, [stations])

  return (
    <div className="page-stack station-page-redesign">
      <section className="stations-hero">
        <div className="stations-hero-copy">
          <h1>Stations</h1>
          <p>Personalized radio stations powered by your favorite artists. Explore, play, and shape the sounds Helix finds for you.</p>
        </div>
        <div className="station-stats">
          <StationStat icon="▥" value={stations.length} label="Stations" />
          <StationStat icon="▶" value={recentlyUpdatedCount} label="Ready to play" />
          <StationStat icon="♡" value="—" label="Liked songs" />
        </div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="station-toolbar" aria-label="Station view controls">
        <div>
          <strong>Your stations</strong>
          <span className="muted">Station creation is hidden for now while the station overhaul is planned.</span>
        </div>
        <div className="station-toolbar-actions">
          <label>
            <span>Sort by</span>
            <select aria-label="Sort stations" defaultValue="recent">
              <option value="recent">Recently Played</option>
              <option value="name">A–Z</option>
              <option value="created">Created</option>
            </select>
          </label>
          <button className="view-toggle active" type="button" aria-label="Grid view">▦</button>
          <button className="view-toggle" type="button" aria-label="List view" disabled title="Placeholder">☰</button>
        </div>
      </section>

      <div className="station-grid-redesign">
        {stations.map((station) => (
          <article className="station-card-redesign" key={station.id}>
            <div className="station-art-wrap">
              <Artwork src={station.cover_url || `/api/stations/${station.id}/cover`} alt={station.name} size="lg" />
              <button className="station-floating-play" type="button" onClick={() => player.run(() => api.playStation(station.id), 'play')} aria-label={`Play ${station.name}`}>
                <IconPlay />
              </button>
            </div>
            <div className="station-card-body">
              <h3>{station.name}</h3>
              <p className="muted">{seedLabel(station)}</p>
              <div className="station-card-actions">
                <button className="primary station-play-button" onClick={() => player.run(() => api.playStation(station.id), 'play')}>
                  <IconPlay /> Play
                </button>
                <button className="ghost station-edit-placeholder" type="button" title="Station editor placeholder">✎ Edit</button>
                <button className="icon-button station-more-placeholder" type="button" title="More station actions placeholder" aria-label="More station actions">⋯</button>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
