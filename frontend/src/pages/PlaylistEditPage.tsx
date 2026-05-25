import { FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { PlaylistDetail, PlaylistTrack, SearchMode, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
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
  const [reorderBusy, setReorderBusy] = useState(false)

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
      return
    }

    const nextTracks = moveTrack(detail.tracks, draggedTrackId, targetId)
    setDraggedTrackId('')
    await persistReorder(nextTracks)
  }

  async function removeTrack(trackId: string) {
    if (!playlistId) return
    setBusyTrackId(trackId)
    setError('')
    try {
      setDetail(normalizeDetail(await api.removePlaylistTrack(playlistId, trackId)))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove track')
    } finally {
      setBusyTrackId('')
    }
  }

  return (
    <div className="page-stack playlist-editor-page">
      <div className="playlist-editor-hero">
        <Link className="back-link" to="/playlists">← Playlists</Link>
        <div className="playlist-editor-heading">
          <Artwork src={playlist?.cover_url} alt={playlist?.name ?? 'Playlist cover'} size="md" />
          <div>
            <div className="eyebrow">Playlist Editor</div>
            <h1>{playlist?.name ?? 'Playlist'}</h1>
            <p className="muted">{detail?.tracks.length ?? playlist?.track_count ?? 0} tracks{isSystemPlaylist ? ' • system playlist' : ''}</p>
          </div>
        </div>
        {playlist ? (
          <button className="primary" onClick={() => player.run(() => api.playPlaylist(playlist.id), 'play')}>
            ▶ Play Playlist
          </button>
        ) : null}
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="playlist-editor-grid">
        <div className="panel playlist-track-panel">
          <div className="section-heading">
            <h2>Tracks</h2>
            <span className="muted">Drag tracks to reorder. Remove tracks from this playlist.</span>
          </div>

          {(detail?.tracks ?? []).length === 0 ? (
            <p className="muted">No tracks in this playlist yet.</p>
          ) : (
            <div className="playlist-track-list">
              {detail?.tracks.map((track, index) => (
                <div
                  className={`playlist-track-row ${draggedTrackId === track.id ? 'dragging' : ''}`}
                  key={track.id}
                  draggable={!isSystemPlaylist && !reorderBusy}
                  onDragStart={() => setDraggedTrackId(track.id)}
                  onDragEnd={() => setDraggedTrackId('')}
                  onDragOver={(event) => {
                    if (!isSystemPlaylist) event.preventDefault()
                  }}
                  onDrop={(event) => {
                    event.preventDefault()
                    void dropTrack(track.id)
                  }}
                >
                  <span className="playlist-track-drag-handle" title={isSystemPlaylist ? 'System playlists cannot be reordered' : 'Drag to reorder'} aria-hidden="true">⋮⋮</span>
                  <span className="playlist-track-position">{index + 1}</span>
                  <Artwork src={trackArtwork(track)} alt={track.title} size="sm" />
                  <div className="playlist-track-meta">
                    <strong>{track.title}</strong>
                    <span className="muted">{track.artist}{track.album ? ` • ${track.album}` : ''}</span>
                  </div>
                  <span className="muted duration-cell">{formatDuration(track.duration_ms)}</span>
                  <button className="danger" disabled={busyTrackId === track.id || reorderBusy} onClick={() => removeTrack(track.id)}>
                    {isSystemPlaylist ? 'Unlike' : 'Remove'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <aside className="panel playlist-add-panel">
          <div className="section-heading">
            <h2>Add tracks</h2>
            <span className="muted">Search Helix and add songs directly.</span>
          </div>

          <div className="search-tabs compact-tabs">
            {SEARCH_MODES.map((mode) => (
              <button key={mode.id} type="button" className={`tab-button ${searchMode === mode.id ? 'active' : ''}`} onClick={() => setSearchMode(mode.id)}>
                {mode.label}
              </button>
            ))}
          </div>

          <form className="playlist-search-form" onSubmit={search}>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search songs to add..." />
            <button className="primary" disabled={searching}>{searching ? 'Searching…' : 'Search'}</button>
          </form>

          <div className="playlist-add-results">
            {searchResults.map((song) => {
              const key = resultKey(song)
              const alreadyAdded = existingKeys.has(key)
              return (
                <div className="playlist-add-row" key={`${song.source ?? ''}-${key}-${song.title}`}>
                  <Artwork src={song.art_url || song.thumbnail_url || song.thumbnail} alt={song.title} size="sm" />
                  <div className="playlist-track-meta">
                    <strong>{song.title}</strong>
                    <span className="muted">{song.artist}{song.album ? ` • ${song.album}` : ''}</span>
                  </div>
                  <button disabled={alreadyAdded || addingKey === key} onClick={() => addSong(song)}>
                    {alreadyAdded ? 'Added' : addingKey === key ? 'Adding…' : 'Add'}
                  </button>
                </div>
              )
            })}
          </div>
        </aside>
      </section>
    </div>
  )
}
