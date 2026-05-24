import { FormEvent, useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { Station } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

export function StationsPage() {
  const player = useOutletContext<ReturnType<typeof usePlayer>>()
  const [stations, setStations] = useState<Station[]>([])
  const [name, setName] = useState('')
  const [seedArtist, setSeedArtist] = useState('')
  const [error, setError] = useState('')

  async function load() {
    try {
      setStations(await api.stations())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load stations')
    }
  }

  useEffect(() => { void load() }, [])

  async function create(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || !seedArtist.trim()) return
    await api.createStation({ name, seed_type: 'artist', seed_artist: seedArtist, seed_title: seedArtist })
    setName('')
    setSeedArtist('')
    await load()
  }

  return (
    <div className="page-stack">
      <div>
        <h1>Stations</h1>
        <p className="muted">Simple station management for the current backend. This page is intentionally ready for the future station overhaul.</p>
      </div>
      {error ? <div className="error-banner">{error}</div> : null}

      <form className="inline-form" onSubmit={create}>
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Station name" />
        <input value={seedArtist} onChange={(event) => setSeedArtist(event.target.value)} placeholder="Seed artist" />
        <button className="primary">Create</button>
      </form>

      <div className="grid-cards">
        {stations.map((station) => (
          <article className="tile-card" key={station.id}>
            <Artwork src={station.cover_url || `/api/stations/${station.id}/cover`} alt={station.name} size="lg" />
            <h3>{station.name}</h3>
            <p className="muted">{station.seed_type}: {station.seed_artist || station.seed_title || 'Unknown seed'}</p>
            <div className="card-actions">
              <button className="primary" onClick={() => player.run(() => api.playStation(station.id), 'play')}>Play</button>
              <button className="danger" onClick={async () => { await api.deleteStation(station.id); await load() }}>Delete</button>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
