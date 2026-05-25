import { FormEvent, useEffect, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { Playlist } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

export function PlaylistsPage() {
  const player = useOutletContext<ReturnType<typeof usePlayer>>()
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState('')

  async function load() {
    try {
      setPlaylists(await api.playlists())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load playlists')
    }
  }

  useEffect(() => { void load() }, [])

  async function create(event: FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    await api.createPlaylist(name.trim())
    setName('')
    await load()
  }

  return (
    <div className="page-stack">
      <div>
        <h1>Playlists</h1>
        <p className="muted">Create, play, and delete playlists. Track editing can be added once the core React structure is settled.</p>
      </div>
      {error ? <div className="error-banner">{error}</div> : null}

      <form className="inline-form" onSubmit={create}>
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="New playlist name" />
        <button className="primary">Create</button>
      </form>

      <div className="grid-cards">
        {playlists.map((playlist) => (
          <article className="tile-card" key={playlist.id}>
            <Link to={`/playlists/${encodeURIComponent(playlist.id)}`} aria-label={`Edit ${playlist.name}`}>
              <Artwork src={playlist.cover_url} alt={`${playlist.name} cover`} size="lg" />
            </Link>
            <h3>{playlist.name}</h3>
            <p className="muted">{playlist.track_count ?? 0} tracks</p>
            <div className="card-actions">
              <Link className="button-link" to={`/playlists/${encodeURIComponent(playlist.id)}`}>Edit</Link>
              <button className="primary" onClick={() => player.run(() => api.playPlaylist(playlist.id), 'play')}>Play</button>
              {!playlist.system_key ? <button className="danger" onClick={async () => { await api.deletePlaylist(playlist.id); await load() }}>Delete</button> : null}
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
