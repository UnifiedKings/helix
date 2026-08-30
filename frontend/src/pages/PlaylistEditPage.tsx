import { FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Capabilities, PlaylistDetail, PlaylistTrack, SearchMode, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
import { ArtistLink } from '../components/ArtistLink'
import { AlbumLink } from '../components/AlbumLink'
import { PlaylistImportModal } from '../components/PlaylistImportModal'
import type { usePlayer } from '../hooks/usePlayer'

const SEARCH_MODES: Array<{ id: SearchMode; label: string }> = [
  { id: 'hybrid', label: 'All' },
  { id: 'subsonic', label: 'Library' },
  { id: 'ytmusic', label: 'YTMusic' },
]

function formatDuration(ms?: number) {
  const totalSeconds = Math.max(0, Math.floor((ms ?? 0) / 1000))
  if (!totalSeconds) return ''
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

function formatSearchDuration(song: SearchSong) {
  if (song.duration_ms) return formatDuration(song.duration_ms)
  if (song.duration_seconds) return formatDuration(song.duration_seconds * 1000)
  return ''
}

function subsonicArtworkUrl(subsonicSongId?: string): string {
  return subsonicSongId ? `/api/art/subsonic/${encodeURIComponent(subsonicSongId)}?size=512` : ''
}

function trackArtwork(track: PlaylistTrack): string {
  return track.art_url || track.thumbnail_url || track.thumbnail || track.thumbnails?.find((thumb) => thumb.url)?.url || subsonicArtworkUrl(track.subsonic_song_id)
}

function normalizeDetail(detail: PlaylistDetail): PlaylistDetail {
  return {
    ...detail,
    playlist: {
      ...detail.playlist,
      cover_url: detail.playlist.cover_url || detail.playlist.thumbnail_url || '',
    },
    tracks: (detail.tracks ?? []).map((track) => ({
      ...track,
      art_url: trackArtwork(track),
    })),
  }
}

export function PlaylistEditPage() {
  const { playlistId = '' } = useParams()
  const player = useOutletContext<ReturnType<typeof usePlayer>>()
  const [detail, setDetail] = useState<PlaylistDetail | null>(null)
  const [error, setError] = useState('')
  const [busyTrackId, setBusyTrackId] = useState('')
  const [query, setQuery] = useState('')
  const [searchMode, setSearchMode] = useState<SearchMode>('hybrid')
  const [searchResults, setSearchResults] = useState<SearchSong[]>([])
  const [searching, setSearching] = useState(false)
  const [addingKey, setAddingKey] = useState('')
  const [draggedTrackId, setDraggedTrackId] = useState('')
  const [dragOverTrackId, setDragOverTrackId] = useState('')
  const [reorderBusy, setReorderBusy] = useState(false)
  const [openTrackMenuId, setOpenTrackMenuId] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [subsonicQueuedTrackIds, setSubsonicQueuedTrackIds] = useState<Set<string>>(new Set())

  const playlist = detail?.playlist
  const isSystemPlaylist = Boolean(playlist?.system_key)

  async function load() {
    if (!playlistId) return
    try {
      setError('')
      setDetail(normalizeDetail(await api.playlist(playlistId)))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load playlist')
    }
  }

  useEffect(() => { void load() }, [playlistId])

  useEffect(() => {
    api.capabilities()
      .then(setCapabilities)
      .catch(() => setCapabilities(null))
  }, [])

  useEffect(() => {
    if (!openTrackMenuId) return

    function closeMenu(event: MouseEvent) {
      const target = event.target as HTMLElement | null
      if (!target?.closest('.playlist-track-menu-wrap')) setOpenTrackMenuId('')
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpenTrackMenuId('')
    }

    document.addEventListener('click', closeMenu)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('click', closeMenu)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [openTrackMenuId])

  const existingKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const track of detail?.tracks ?? []) {
      if (track.subsonic_song_id) keys.add(`subsonic:${track.subsonic_song_id}`)
      if (track.yt_video_id) keys.add(`yt:${track.yt_video_id}`)
      if (track.key) keys.add(track.key)
      keys.add(`text:${track.title}|${track.artist}`)
    }
    return keys
  }, [detail])

  function resultKey(song: SearchSong) {
    if (song.subsonic_song_id) return `subsonic:${song.subsonic_song_id}`
    const videoId = song.yt_video_id || song.video_id || song.videoId
    if (videoId) return `yt:${videoId}`
    return `text:${song.title}|${song.artist}`
  }

  async function search(event: FormEvent) {
    event.preventDefault()
    if (!query.trim()) return
    setSearching(true)
    setError('')
    try {
      const response = await api.search(query.trim(), searchMode)
      setSearchResults(response.songs ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setSearching(false)
    }
  }

  async function addSong(song: SearchSong) {
    if (!playlistId) return
    const key = resultKey(song)
    setAddingKey(key)
    setError('')
    try {
      setDetail(normalizeDetail(await api.addSongToPlaylist(playlistId, song)))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add track')
    } finally {
      setAddingKey('')
    }
  }

  function moveTrack(tracks: PlaylistTrack[], draggedId: string, targetId: string): PlaylistTrack[] {
    const fromIndex = tracks.findIndex((track) => track.id === draggedId)
    const toIndex = tracks.findIndex((track) => track.id === targetId)
    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return tracks

    const next = [...tracks]
    const [moved] = next.splice(fromIndex, 1)
    next.splice(toIndex, 0, moved)
    return next.map((track, index) => ({ ...track, position: index }))
  }

  async function persistReorder(nextTracks: PlaylistTrack[]) {
    if (!playlistId || !detail) return

    const previous = detail
    setReorderBusy(true)
    setError('')
    setDetail(normalizeDetail({ ...detail, tracks: nextTracks }))

    try {
      setDetail(normalizeDetail(await api.reorderPlaylistTracks(playlistId, nextTracks.map((track) => track.id))))
    } catch (err) {
      setDetail(previous)
      setError(err instanceof Error ? err.message : 'Could not reorder playlist')
    } finally {
      setReorderBusy(false)
    }
  }

  async function dropTrack(targetId: string) {
    if (!detail || isSystemPlaylist || reorderBusy || !draggedTrackId || draggedTrackId === targetId) {
      setDraggedTrackId('')
      setDragOverTrackId('')
      return
    }

    const nextTracks = moveTrack(detail.tracks, draggedTrackId, targetId)
    setDraggedTrackId('')
    setDragOverTrackId('')
    await persistReorder(nextTracks)
  }

  async function removeTrack(trackId: string) {
    if (!playlistId) return
    setBusyTrackId(trackId)
    setOpenTrackMenuId('')
    setError('')
    try {
      setDetail(normalizeDetail(await api.removePlaylistTrack(playlistId, trackId)))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove track')
    } finally {
      setBusyTrackId('')
    }
  }

  function playTrack(track: PlaylistTrack) {
    setOpenTrackMenuId('')
    player.run(() => api.playSong(track), 'play')
  }

  function queueTrack(track: PlaylistTrack) {
    setOpenTrackMenuId('')
    player.run(() => api.queueSong(track))
  }

  async function addTrackToSubsonic(track: PlaylistTrack) {
    if (subsonicQueuedTrackIds.has(track.id)) return
    setOpenTrackMenuId('')
    setError('')
    setSubsonicQueuedTrackIds((current) => new Set(current).add(track.id))
    try {
      await api.addSongToSubsonic(track)
    } catch (err) {
      setSubsonicQueuedTrackIds((current) => {
        const next = new Set(current)
        next.delete(track.id)
        return next
      })
      setError(err instanceof Error ? err.message : 'Could not add track to Subsonic')
    }
  }

  return (
    <div className="page-stack playlist-editor-page">
      <Link className="playlist-editor-back" to="/playlists">← Playlists</Link>

      <header className="playlist-editor-header">
        <div className="playlist-editor-cover">
          <Artwork src={playlist?.cover_url} alt={playlist?.name ?? 'Playlist cover'} size="md" />
        </div>
        <div className="playlist-editor-summary">
          <h1>{playlist?.name ?? 'Playlist'}</h1>
          <p className="playlist-editor-count">{detail?.tracks.length ?? playlist?.track_count ?? 0} tracks{isSystemPlaylist ? ' • system playlist' : ''}</p>
        </div>
        {playlist ? (
          <div className="playlist-editor-controls">
            <button className="primary" onClick={() => player.run(() => api.playPlaylist(playlist.id), 'play')}>
              ▶ Play Playlist
            </button>
            <button onClick={() => player.run(() => api.playPlaylist(playlist.id, true), 'play')}>
              Shuffle Play
            </button>
            <button type="button" onClick={() => setImportOpen(true)}>
              Import playlist
            </button>
          </div>
        ) : null}
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="playlist-editor-layout">
        <div className="playlist-editor-track-section">
          <div className="playlist-editor-section-heading">
            <h2>Tracks</h2>
            <span className="muted">{isSystemPlaylist ? 'System playlist order is managed automatically.' : 'Drag to reorder'}</span>
          </div>

          {(detail?.tracks ?? []).length === 0 ? (
            <p className="playlist-editor-empty">No tracks in this playlist yet.</p>
          ) : (
            <div className="playlist-editor-track-list">
              {detail?.tracks.map((track, index) => (
                <div
                  className={`playlist-editor-track-row ${draggedTrackId === track.id ? 'dragging' : ''} ${dragOverTrackId === track.id && draggedTrackId !== track.id ? 'drop-target' : ''}`}
                  key={track.id}
                  draggable={!isSystemPlaylist && !reorderBusy}
                  onDragStart={() => {
                    setDraggedTrackId(track.id)
                    setOpenTrackMenuId('')
                  }}
                  onDragEnd={() => {
                    setDraggedTrackId('')
                    setDragOverTrackId('')
                  }}
                  onDragEnter={() => {
                    if (!isSystemPlaylist && draggedTrackId && draggedTrackId !== track.id) setDragOverTrackId(track.id)
                  }}
                  onDragOver={(event) => {
                    if (!isSystemPlaylist) event.preventDefault()
                  }}
                  onDrop={(event) => {
                    event.preventDefault()
                    void dropTrack(track.id)
                  }}
                >
                  <span className="playlist-editor-drag-handle" title={isSystemPlaylist ? 'System playlists cannot be reordered' : 'Drag to reorder'} aria-hidden="true">⠿</span>
                  <span className="playlist-editor-track-number">{index + 1}</span>
                  <Artwork src={trackArtwork(track)} alt={track.title} size="sm" />
                  <div className="playlist-editor-track-meta">
                    <strong>{track.title}</strong>
                    <span className="muted"><ArtistLink artist={track.artist} />{track.album ? <> • <AlbumLink album={track.album} artist={track.artist} source={track.source} /></> : null}</span>
                  </div>
                  <span className="playlist-editor-duration">{formatDuration(track.duration_ms)}</span>
                  <div className="playlist-track-menu-wrap">
                    <button
                      type="button"
                      className="playlist-track-menu-button"
                      aria-label={`More options for ${track.title}`}
                      aria-expanded={openTrackMenuId === track.id}
                      onClick={(event) => {
                        event.stopPropagation()
                        setOpenTrackMenuId((current) => current === track.id ? '' : track.id)
                      }}
                    >
                      ⋯
                    </button>
                    {openTrackMenuId === track.id ? (
                      <div className="playlist-track-menu-popover" role="menu" onClick={(event) => event.stopPropagation()}>
                        <button type="button" role="menuitem" onClick={() => playTrack(track)}>
                          <span className="playlist-track-menu-icon" aria-hidden="true">▶</span>
                          <span>Play now</span>
                        </button>
                        <button type="button" role="menuitem" onClick={() => queueTrack(track)}>
                          <span className="playlist-track-menu-icon" aria-hidden="true">+</span>
                          <span>Add to queue</span>
                        </button>
                        {capabilities?.features.subsonic_import && track.source !== 'subsonic' && !track.subsonic_song_id ? (
                          <button
                            type="button"
                            role="menuitem"
                            disabled={subsonicQueuedTrackIds.has(track.id)}
                            onClick={() => void addTrackToSubsonic(track)}
                          >
                            <span className="playlist-track-menu-icon playlist-track-menu-icon-subsonic" aria-hidden="true">S+</span>
                            <span>{subsonicQueuedTrackIds.has(track.id) ? 'Queued for Subsonic' : 'Add to Subsonic'}</span>
                          </button>
                        ) : null}
                        <div className="playlist-track-menu-divider" />
                        <button
                          type="button"
                          role="menuitem"
                          className="danger"
                          disabled={busyTrackId === track.id || reorderBusy}
                          onClick={() => void removeTrack(track.id)}
                        >
                          <span>{isSystemPlaylist ? 'Unlike track' : 'Remove from playlist'}</span>
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <aside className="playlist-editor-add-panel">
          <div className="playlist-editor-add-header">
            <h2>Add tracks</h2>
            <p className="muted">Search Helix and add songs directly.</p>
          </div>

          <div className="search-tabs playlist-editor-tabs">
            {SEARCH_MODES.map((mode) => (
              <button key={mode.id} type="button" className={`tab-button ${searchMode === mode.id ? 'active' : ''}`} onClick={() => setSearchMode(mode.id)}>
                {mode.label}
              </button>
            ))}
          </div>

          <form className="playlist-editor-search-form" onSubmit={search}>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search songs to add..." />
            <button className="primary" disabled={searching}>{searching ? 'Searching…' : 'Search'}</button>
          </form>

          <div className="playlist-editor-add-results">
            {searchResults.map((song) => {
              const key = resultKey(song)
              const alreadyAdded = existingKeys.has(key)
              return (
                <div className="playlist-editor-add-row" key={`${song.source ?? ''}-${key}-${song.title}`}>
                  <Artwork src={song.art_url || song.thumbnail_url || song.thumbnail} alt={song.title} size="sm" />
                  <div className="playlist-editor-track-meta">
                    <strong>{song.title}</strong>
                    <span className="muted"><ArtistLink artist={song.artist} />{song.album ? <> • <AlbumLink album={song.album} artist={song.artist} source={song.source} /></> : null}</span>
                  </div>
                  <span className="playlist-editor-add-duration">{formatSearchDuration(song)}</span>
                  <button disabled={alreadyAdded || addingKey === key} onClick={() => void addSong(song)}>
                    {alreadyAdded ? 'Added' : addingKey === key ? 'Adding…' : 'Add'}
                  </button>
                </div>
              )
            })}
          </div>
        </aside>
      </section>

      <PlaylistImportModal
        open={importOpen}
        playlistId={playlistId}
        playlistName={playlist?.name ?? 'playlist'}
        onClose={() => setImportOpen(false)}
        onImported={load}
      />
    </div>
  )
}
