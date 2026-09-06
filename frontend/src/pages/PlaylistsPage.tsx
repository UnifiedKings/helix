import { FormEvent, useEffect, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { Capabilities, Playlist } from '../api/types'
import { Artwork } from '../components/Artwork'
import { PlaylistImportModal } from '../components/PlaylistImportModal'
import type { usePlayer } from '../hooks/usePlayer'
import '../styles/create-playlist-modal.css'

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


function downloadJson(filename: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function safeFilename(name: string) {
  const cleaned = (name || 'playlist').replace(/[\\/:*?"<>|]+/g, '').trim().replace(/\s+/g, '_')
  return `${cleaned || 'playlist'}.json`
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
  const [createWithImport, setCreateWithImport] = useState(false)
  const [importTarget, setImportTarget] = useState<Playlist | null>(null)
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

  useEffect(() => {
    if (!createOpen) return
    function closeCreateOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape' && !creating) closeCreateModal()
    }
    document.addEventListener('keydown', closeCreateOnEscape)
    return () => document.removeEventListener('keydown', closeCreateOnEscape)
  }, [createOpen, creating])

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

  function closeCreateModal() {
    if (creating) return
    setCreateOpen(false)
    setName('')
    setCreateWithImport(false)
  }

  async function create(event: FormEvent) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName || creating) return

    setCreating(true)
    setError('')
    try {
      const created = await api.createPlaylist(trimmedName)
      const shouldImport = createWithImport
      setName('')
      setCreateOpen(false)
      setCreateWithImport(false)
      await load()
      if (shouldImport) setImportTarget(created)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create playlist')
    } finally {
      setCreating(false)
    }
  }

  async function exportPlaylist(playlist: Playlist) {
    closePlaylistMenus()
    setError('')
    try {
      const payload = await api.exportPlaylist(playlist.id)
      downloadJson(safeFilename(playlist.name), payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not export playlist')
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
            onClick={() => setCreateOpen(true)}
            aria-expanded={createOpen}
          >
            <span aria-hidden="true">＋</span>
            New playlist
          </button>
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="status-banner">{status}</div> : null}

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
                    <button
                      type="button"
                      className="menu-link"
                      onClick={() => void exportPlaylist(playlist)}
                    >
                      Export playlist
                    </button>
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

      {createOpen ? (
        <div
          className="playlist-create-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeCreateModal()
          }}
        >
          <form className="playlist-create-modal" onSubmit={create} role="dialog" aria-modal="true" aria-labelledby="playlist-create-title">
            <header className="playlist-create-header">
              <div>
                <p className="playlist-create-eyebrow">Playlist</p>
                <h2 id="playlist-create-title">Create playlist</h2>
              </div>
              <button type="button" className="playlist-create-close" onClick={closeCreateModal} disabled={creating} aria-label="Close create playlist">×</button>
            </header>

            <div className="playlist-create-body">
              <label className="playlist-create-field">
                <span>Name</span>
                <input
                  autoFocus
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Playlist name"
                  aria-label="Playlist name"
                />
              </label>

              <div className="playlist-create-section">
                <div className="playlist-create-section-copy">
                  <strong>How do you want to start?</strong>
                  <span>You can always import more music later.</span>
                </div>

                <div className="playlist-create-choice-grid">
                  <button
                    type="button"
                    className={`playlist-create-choice ${!createWithImport ? 'active' : ''}`}
                    onClick={() => setCreateWithImport(false)}
                    aria-pressed={!createWithImport}
                  >
                    <span className="playlist-create-choice-icon" aria-hidden="true">♫</span>
                    <span>
                      <strong>Start empty</strong>
                      <small>Create the playlist and add tracks yourself.</small>
                    </span>
                  </button>

                  <button
                    type="button"
                    className={`playlist-create-choice ${createWithImport ? 'active' : ''}`}
                    onClick={() => setCreateWithImport(true)}
                    aria-pressed={createWithImport}
                  >
                    <span className="playlist-create-choice-icon" aria-hidden="true">⇩</span>
                    <span>
                      <strong>Import tracks</strong>
                      <small>Create it, then open Helix's full import workflow.</small>
                    </span>
                  </button>
                </div>
              </div>

              {createWithImport ? (
                <div className="playlist-create-import-panel">
                  <div>
                    <strong>Import after creation</strong>
                    <p>
                      Helix will immediately open the existing importer, including match review,
                      duplicate skipping, alternate matches, and source-specific upload/URL handling.
                    </p>
                  </div>
                  <div className="playlist-create-source-chips" aria-label="Supported playlist import sources">
                    <span>Helix</span>
                    <span>YTMusic</span>
                    <span>Spotify</span>
                    <span>Pandora</span>
                  </div>
                </div>
              ) : null}
            </div>

            <footer className="playlist-create-footer">
              <button type="button" onClick={closeCreateModal} disabled={creating}>Cancel</button>
              <button type="submit" className="primary" disabled={creating || !name.trim()}>
                {creating ? 'Creating…' : createWithImport ? 'Create & import' : 'Create playlist'}
              </button>
            </footer>
          </form>
        </div>
      ) : null}

      <PlaylistImportModal
        open={Boolean(importTarget)}
        playlistId={importTarget?.id ?? ''}
        playlistName={importTarget?.name ?? ''}
        onClose={() => setImportTarget(null)}
        onImported={async () => {
          await load()
          setImportTarget(null)
        }}
      />
    </div>
  )
}
