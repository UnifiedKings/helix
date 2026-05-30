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
  const [creating, setCreating] = useState(false)

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
    const trimmedName = name.trim()
    if (!trimmedName || creating) return

    setCreating(true)
    setError('')
    try {
      await api.createPlaylist(trimmedName)
      setName('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create playlist')
    } finally {
      setCreating(false)
    }
  }

  async function deletePlaylist(playlist: Playlist) {
    const confirmed = window.confirm(`Delete playlist "${playlist.name}"? This cannot be undone.`)
    if (!confirmed) return

    setError('')
    try {
      await api.deletePlaylist(playlist.id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete playlist')
    }
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
        <button type="submit" className="primary" disabled={creating || !name.trim()}>{creating ? 'Creating…' : 'Create'}</button>
      </form>

      <div className="grid-cards">
        {playlists.map((playlist) => (
          <article className="tile-card" key={playlist.id}>
            <Link to={`/playlists/${encodeURIComponent(playlist.id)}`} aria-label={`Edit ${playlist.name}`}>
              <Artwork src={playlist.cover_url} alt={`${playlist.name} cover`} size="lg" />
            </Link>
            <h3>{playlist.name}</h3>
            <p className="muted">{playlist.track_count ?? 0} tracks</p>
            <div className="card-actions playlist-card-actions">
              <button
                className="playlist-card-action playlist-card-action-primary"
                aria-label={`Play ${playlist.name}`}
                title="Play"
                data-tooltip="Play"
                onClick={() => player.run(() => api.playPlaylist(playlist.id), 'play')}
              >
                ▶
              </button>
              <button
                className="playlist-card-action"
                aria-label={`Shuffle ${playlist.name}`}
                title="Shuffle"
                data-tooltip="Shuffle"
                onClick={() => player.run(() => api.playPlaylist(playlist.id, true), 'play')}
              >
                ⤨
              </button>
              <details className="album-card-menu playlist-card-menu">
                <summary className="playlist-card-action playlist-more-button" aria-label={`More options for ${playlist.name}`} title="More options" data-tooltip="More options">⋯</summary>
                <div className="album-card-menu-popover playlist-card-menu-popover">
                  <Link className="menu-link" to={`/playlists/${encodeURIComponent(playlist.id)}`}>Edit playlist</Link>
                  {!playlist.system_key ? (
                    <button type="button" className="menu-danger" onClick={() => void deletePlaylist(playlist)}>Delete playlist</button>
                  ) : null}
                </div>
              </details>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
