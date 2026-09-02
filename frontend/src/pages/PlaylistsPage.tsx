import { FormEvent, useEffect, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { Capabilities, Playlist } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlaylistSubsonicResult = {
  ok: boolean
  playlist_id: string
  playlist_name: string
  total: number
  enqueued: number
  skipped_existing: number
  unresolved: number
  unresolved_tracks: string[]
  lookup_failed: number
  lookup_failed_tracks: string[]
}

async function addPlaylistToSubsonicRequest(playlistId: string): Promise<PlaylistSubsonicResult> {
  const response = await fetch(`/api/subsonic/add/playlist/${encodeURIComponent(playlistId)}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  })

  const text = await response.text()
  if (!response.ok) {
    let message = text || `${response.status} ${response.statusText}`
    try {
      message = JSON.parse(text).detail ?? message
    } catch {
      // Keep the raw response text.
    }
    throw new Error(message)
  }

  return JSON.parse(text) as PlaylistSubsonicResult
}

export function PlaylistsPage() {
  const player = useOutletContext<ReturnType<typeof usePlayer>>()
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [creating, setCreating] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [subsonicBusyPlaylistId, setSubsonicBusyPlaylistId] = useState('')

  async function load() {
    try {
      setPlaylists(await api.playlists())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load playlists')
    }
  }

  useEffect(() => { void load() }, [])

  useEffect(() => {
    api.capabilities()
      .then(setCapabilities)
      .catch(() => setCapabilities(null))
  }, [])

  useEffect(() => {
    function closeOpenPlaylistMenus(event: PointerEvent) {
      const target = event.target as Element | null
      if (target?.closest('.playlist-library-menu')) return

      document.querySelectorAll<HTMLDetailsElement>('details.playlist-library-menu[open]').forEach((menu) => {
        menu.open = false
      })
    }

    function closePlaylistMenusOnEscape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      document.querySelectorAll<HTMLDetailsElement>('details.playlist-library-menu[open]').forEach((menu) => {
        menu.open = false
      })
    }

    document.addEventListener('pointerdown', closeOpenPlaylistMenus)
    document.addEventListener('keydown', closePlaylistMenusOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOpenPlaylistMenus)
      document.removeEventListener('keydown', closePlaylistMenusOnEscape)
    }
  }, [])

  function closeOtherPlaylistMenus(current: HTMLDetailsElement) {
    document.querySelectorAll<HTMLDetailsElement>('details.playlist-library-menu[open]').forEach((menu) => {
      if (menu !== current) menu.open = false
    })
  }

  function closePlaylistMenus() {
    document.querySelectorAll<HTMLDetailsElement>('details.playlist-library-menu[open]').forEach((menu) => {
      menu.open = false
    })
  }

  async function create(event: FormEvent) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName || creating) return

    setCreating(true)
    setError('')
    try {
      await api.createPlaylist(trimmedName)
      setName('')
      setCreateOpen(false)
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

  async function addPlaylistToSubsonic(playlist: Playlist) {
    if (subsonicBusyPlaylistId) return

    closePlaylistMenus()
    setSubsonicBusyPlaylistId(playlist.id)
    setError('')
    setStatus('')

    try {
      const result = await addPlaylistToSubsonicRequest(playlist.id)
      const parts: string[] = []
      if (result.enqueued > 0) parts.push(`${result.enqueued} queued`)
      if (result.skipped_existing > 0) parts.push(`${result.skipped_existing} already in Subsonic`)
      if (result.unresolved > 0) parts.push(`${result.unresolved} unresolved`)
      if (result.lookup_failed > 0) parts.push(`${result.lookup_failed} could not be checked`)

      setStatus(
        parts.length
          ? `${playlist.name}: ${parts.join(' • ')}`
          : `${playlist.name}: no tracks needed to be added`,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add playlist to Subsonic')
    } finally {
      setSubsonicBusyPlaylistId('')
    }
  }

  const orderedPlaylists = [...playlists].sort(
    (left, right) => Number(Boolean(right.system_key)) - Number(Boolean(left.system_key)),
  )

  return (
    <div className="playlists-library-page">
      <header className="playlists-library-header">
        <div>
          <h1>Playlists</h1>
          <p className="muted">Your playlists</p>
        </div>
        <div className="playlists-library-header-actions">
          <span className="playlists-library-count">{playlists.length} {playlists.length === 1 ? 'playlist' : 'playlists'}</span>
          <button
            type="button"
            className="playlists-new-button"
            onClick={() => setCreateOpen((open) => !open)}
            aria-expanded={createOpen}
          >
            <span aria-hidden="true">＋</span>
            New playlist
          </button>
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="status-banner">{status}</div> : null}

      {createOpen ? (
        <form className="playlists-create-form" onSubmit={create}>
          <input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Playlist name"
            aria-label="Playlist name"
          />
          <button type="submit" className="primary" disabled={creating || !name.trim()}>
            {creating ? 'Creating…' : 'Create playlist'}
          </button>
          <button
            type="button"
            className="playlists-create-cancel"
            onClick={() => {
              setCreateOpen(false)
              setName('')
            }}
          >
            Cancel
          </button>
        </form>
      ) : null}

      <section className="playlists-library-section" aria-label="Your playlists">
        <div className="playlists-library-grid">
          {orderedPlaylists.map((playlist) => (
            <article className="playlist-library-card" key={playlist.id}>
              <div className="playlist-library-art-wrap">
                <Link
                  className="playlist-library-art-link"
                  to={`/playlists/${encodeURIComponent(playlist.id)}`}
                  aria-label={`Open ${playlist.name}`}
                >
                  <Artwork src={playlist.cover_url} alt={`${playlist.name} cover`} size="lg" />
                </Link>
                <button
                  type="button"
                  className="playlist-library-play"
                  aria-label={`Play ${playlist.name}`}
                  title={`Play ${playlist.name}`}
                  onClick={() => player.run(() => api.playPlaylist(playlist.id), 'play')}
                >
                  ▶
                </button>
              </div>

              <div className="playlist-library-meta-row">
                <Link className="playlist-library-title" to={`/playlists/${encodeURIComponent(playlist.id)}`}>
                  {playlist.name}
                </Link>
                <details
                  className="album-card-menu playlist-library-menu"
                  onToggle={(event) => {
                    if (event.currentTarget.open) closeOtherPlaylistMenus(event.currentTarget)
                  }}
                >
                  <summary aria-label={`More options for ${playlist.name}`} title="More options">⋯</summary>
                  <div className="album-card-menu-popover playlist-card-menu-popover">
                    <button
                      type="button"
                      className="menu-link"
                      onClick={() => player.run(() => api.playPlaylist(playlist.id, true), 'play')}
                    >
                      Shuffle
                    </button>
                    <Link className="menu-link" to={`/playlists/${encodeURIComponent(playlist.id)}`}>Edit playlist</Link>
                    {capabilities?.features.subsonic_import ? (
                      <button
                        type="button"
                        className="menu-link"
                        disabled={subsonicBusyPlaylistId === playlist.id}
                        onClick={() => void addPlaylistToSubsonic(playlist)}
                      >
                        {subsonicBusyPlaylistId === playlist.id ? 'Checking Subsonic…' : 'Add to Subsonic'}
                      </button>
                    ) : null}
                    {!playlist.system_key ? (
                      <button type="button" className="menu-danger" onClick={() => void deletePlaylist(playlist)}>Delete playlist</button>
                    ) : null}
                  </div>
                </details>
              </div>
              <p className="playlist-library-track-count">{playlist.track_count ?? 0} tracks</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
