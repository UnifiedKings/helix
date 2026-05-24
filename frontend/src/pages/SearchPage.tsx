import { FormEvent, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { SearchAlbum, SearchMode, SearchResponse, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

const SEARCH_MODES: Array<{ id: SearchMode; label: string; description: string }> = [
  {
    id: 'hybrid',
    label: 'All',
    description: 'Best local Subsonic matches first, then YTMusic discovery results.',
  },
  {
    id: 'subsonic',
    label: 'Library',
    description: 'Only search music already available from your Subsonic library.',
  },
  {
    id: 'ytmusic',
    label: 'YTMusic',
    description: 'Only search YTMusic discovery results.',
  },
]

function SourceBadge({ source }: { source?: string }) {
  if (!source) return null
  const label = source === 'subsonic' ? 'Subsonic' : source === 'ytmusic' ? 'YTMusic' : source
  return <span className={`badge ${source === 'subsonic' ? 'good' : ''}`}>{label}</span>
}

function SongCard({ song, player }: { song: SearchSong; player: PlayerContext }) {
  return (
    <article className="media-card">
      <Artwork src={song.art_url || song.thumbnail_url || song.thumbnail} alt={song.title} />
      <div className="media-body">
        <div className="media-title">{song.title}</div>
        <div className="muted">{song.artist}{song.album ? ` • ${song.album}` : ''}</div>
        <SourceBadge source={song.source} />
      </div>
      <div className="card-actions">
        <button className="primary" onClick={() => player.run(() => api.playSong(song), 'play')}>Play</button>
        <button onClick={() => player.run(() => api.queueSong(song))}>Queue</button>
      </div>
    </article>
  )
}

function AlbumCard({ album, player }: { album: SearchAlbum; player: PlayerContext }) {
  return (
    <article className="media-card">
      <Artwork src={album.art_url || album.thumbnail_url || album.thumbnail} alt={album.title} />
      <div className="media-body">
        <div className="media-title">{album.title}</div>
        <div className="muted">{album.artist ?? 'Unknown artist'}{album.year ? ` • ${album.year}` : ''}</div>
        <SourceBadge source={album.source} />
      </div>
      <div className="card-actions">
        <button className="primary" onClick={() => player.run(() => api.playAlbum(album), 'play')}>Play</button>
        <button onClick={() => player.run(() => api.queueAlbum(album))}>Queue</button>
      </div>
    </article>
  )
}

export function SearchPage() {
  const player = useOutletContext<PlayerContext>()
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<SearchMode>('hybrid')
  const [results, setResults] = useState<SearchResponse>({ mode: 'hybrid', songs: [], albums: [] })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function runSearch(nextMode = mode, nextQuery = query.trim()) {
    if (!nextQuery) return
    setLoading(true)
    setError('')
    try {
      setResults(await api.search(nextQuery, nextMode))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setLoading(false)
    }
  }

  async function search(event: FormEvent) {
    event.preventDefault()
    await runSearch()
  }

  async function selectMode(nextMode: SearchMode) {
    setMode(nextMode)
    if (query.trim()) {
      await runSearch(nextMode)
    }
  }

  const selectedMode = SEARCH_MODES.find((item) => item.id === mode) ?? SEARCH_MODES[0]

  return (
    <div className="page-stack">
      <div>
        <h1>Search</h1>
        <p className="muted">The browser asks Helix to search. YTMusic and Subsonic access stays behind the backend.</p>
      </div>

      <div className="search-tabs" role="tablist" aria-label="Search mode">
        {SEARCH_MODES.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`tab-button ${mode === item.id ? 'active' : ''}`}
            onClick={() => void selectMode(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <p className="muted mode-description">{selectedMode.description}</p>

      <form className="search-form" onSubmit={search}>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search songs, albums, artists..." />
        <button className="primary" disabled={loading}>{loading ? 'Searching...' : 'Search'}</button>
      </form>

      {error ? <div className="error-banner">{error}</div> : null}

      <section>
        <h2>Songs</h2>
        <div className="card-list">
          {results.songs.map((song, index) => <SongCard key={`${song.source}-${song.title}-${song.artist}-${index}`} song={song} player={player} />)}
          {!loading && results.songs.length === 0 ? <p className="muted">No song results yet.</p> : null}
        </div>
      </section>

      <section>
        <h2>Albums</h2>
        <div className="card-list">
          {results.albums.map((album, index) => <AlbumCard key={`${album.source}-${album.title}-${album.artist}-${index}`} album={album} player={player} />)}
          {!loading && results.albums.length === 0 ? <p className="muted">No album results yet.</p> : null}
        </div>
      </section>
    </div>
  )
}
