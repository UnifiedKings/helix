import { FormEvent, useMemo, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Link, useLocation, useNavigate, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { SearchAlbum, SearchArtist, SearchMode, SearchResponse, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

type SearchReturnState = {
  query: string
  mode: SearchMode
  results: SearchResponse
  artists: SearchArtist[]
}

type SearchRouteState = {
  searchReturn?: SearchReturnState
}

const SEARCH_MODES: Array<{ id: SearchMode; label: string; description: string }> = [
  { id: 'hybrid', label: 'All', description: 'Local Subsonic matches first, then YTMusic discovery.' },
  { id: 'subsonic', label: 'Library', description: 'Only music already available in your Subsonic library.' },
  { id: 'ytmusic', label: 'YTMusic', description: 'Only YTMusic discovery results.' },
]

function SourceBadge({ source }: { source?: string }) {
  if (!source) return null
  const label = source === 'subsonic' ? 'Subsonic' : source === 'ytmusic' ? 'YTMusic' : source
  return <span className={`badge ${source === 'subsonic' ? 'good' : ''}`}>{label}</span>
}

function durationLabel(item: Pick<SearchSong, 'duration_ms' | 'duration_seconds'>) {
  const rawSeconds = item.duration_seconds ?? (item.duration_ms ? Math.round(item.duration_ms / 1000) : 0)
  if (!rawSeconds) return ''
  const minutes = Math.floor(rawSeconds / 60)
  const seconds = rawSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

function resultArtwork(item: SearchSong | SearchAlbum | SearchArtist) {
  return item.art_url || item.thumbnail_url || ''
}

function albumBrowseId(album: SearchAlbum) {
  return album.yt_browse_id || album.browse_id || album.browseId || album.subsonic_album_id || ''
}

function artistBrowseId(artist: SearchArtist) {
  return artist.browse_id || artist.artist_id || ''
}

function topResult(results: SearchResponse): { kind: 'song'; item: SearchSong } | { kind: 'album'; item: SearchAlbum } | null {
  const subsonicAlbum = results.albums.find((album) => album.source === 'subsonic')
  if (subsonicAlbum) return { kind: 'album', item: subsonicAlbum }
  const subsonicSong = results.songs.find((song) => song.source === 'subsonic')
  if (subsonicSong) return { kind: 'song', item: subsonicSong }
  if (results.albums[0]) return { kind: 'album', item: results.albums[0] }
  if (results.songs[0]) return { kind: 'song', item: results.songs[0] }
  return null
}

function TopResultCard({ result, player, onStatus, searchReturn }: { result: NonNullable<ReturnType<typeof topResult>>; player: PlayerContext; onStatus: (message: string) => void; searchReturn: SearchReturnState }) {
  const item = result.item
  const title = item.title
  const isYt = item.source === 'ytmusic' || (!item.source && result.kind === 'album')
  const subtitle = result.kind === 'album'
    ? `${result.item.artist ?? 'Unknown artist'}${result.item.year ? ` • ${result.item.year}` : ''}`
    : `${result.item.artist}${result.item.album ? ` • ${result.item.album}` : ''}`

  async function addToSubsonic() {
    if (!isYt) return
    if (result.kind === 'album') {
      await api.addAlbumToSubsonic(result.item)
      onStatus(`Queued album for Subsonic import: ${result.item.title}`)
    } else {
      await api.addSongToSubsonic(result.item)
      onStatus(`Queued track for Subsonic import: ${result.item.title}`)
    }
  }

  return (
    <section className="search-top-result">
      <div className="search-section-heading"><span>Top result</span></div>
      <article className="top-result-card">
        <Artwork src={resultArtwork(item)} alt={title} size="lg" />
        <div className="top-result-copy">
          <span className="result-kind">{result.kind}</span>
          <h2>{title}</h2>
          <p>{subtitle}</p>
          <SourceBadge source={item.source} />
        </div>
        <div className="top-result-actions">
          <button className="primary" onClick={() => result.kind === 'album' ? player.run(() => api.playAlbum(result.item), 'play') : player.run(() => api.playSong(result.item), 'play')}>▶ Play</button>
          <button onClick={() => result.kind === 'album' ? player.run(() => api.queueAlbum(result.item)) : player.run(() => api.queueSong(result.item))}>Queue</button>
          {result.kind === 'album' && albumBrowseId(result.item) ? <Link className="button-link" to={`/albums/${encodeURIComponent(albumBrowseId(result.item))}`} state={{ searchReturn }}>Open album</Link> : null}
          {isYt ? <button onClick={() => void addToSubsonic()}>Add to Subsonic</button> : null}
        </div>
      </article>
    </section>
  )
}

function SongRow({ song, player, onStatus }: { song: SearchSong; player: PlayerContext; onStatus: (message: string) => void }) {
  const duration = durationLabel(song)
  const canAdd = song.source === 'ytmusic' || Boolean(song.yt_video_id || song.video_id || song.videoId)

  async function addToSubsonic() {
    await api.addSongToSubsonic(song)
    onStatus(`Queued track for Subsonic import: ${song.title}`)
  }

  return (
    <article className="search-song-row">
      <Artwork src={resultArtwork(song)} alt={song.title} size="sm" />
      <div className="song-title-cell"><strong>{song.title}</strong><span>{song.artist}{song.album ? ` • ${song.album}` : ''}</span></div>
      <SourceBadge source={song.source} />
      <span className="song-duration">{duration}</span>
      <div className="search-row-actions">
        <button className="icon-button compact-action" aria-label={`Play ${song.title}`} title="Play" onClick={() => player.run(() => api.playSong(song), 'play')}>▶</button>
        <button className="icon-button compact-action" aria-label={`Queue ${song.title}`} title="Queue" onClick={() => player.run(() => api.queueSong(song))}>＋</button>
        {canAdd && song.source !== 'subsonic' ? <button className="compact-text-action" onClick={() => void addToSubsonic()}>Add</button> : null}
      </div>
    </article>
  )
}

function AlbumCard({ album, player, onStatus, searchReturn }: { album: SearchAlbum; player: PlayerContext; onStatus: (message: string) => void; searchReturn: SearchReturnState }) {
  const navigate = useNavigate()
  const browseId = albumBrowseId(album)
  const albumPath = browseId ? `/albums/${encodeURIComponent(browseId)}` : ''
  const canAdd = album.source === 'ytmusic' || Boolean(browseId)

  function openAlbum() {
    if (albumPath) navigate(albumPath, { state: { searchReturn } })
  }

  function handleAlbumKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!albumPath) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openAlbum()
    }
  }

  async function addToSubsonic() {
    await api.addAlbumToSubsonic(album)
    onStatus(`Queued album for Subsonic import: ${album.title}`)
  }

  return (
    <article
      className={`search-album-card ${albumPath ? 'search-album-card-clickable' : ''}`}
      onClick={openAlbum}
      onKeyDown={handleAlbumKeyDown}
      role={albumPath ? 'button' : undefined}
      tabIndex={albumPath ? 0 : undefined}
      aria-label={albumPath ? `Open album ${album.title}` : undefined}
    >
      <Artwork src={resultArtwork(album)} alt={album.title} size="lg" />
      <div className="album-card-body">
        <strong>{album.title}</strong>
        <span>{album.artist ?? 'Unknown artist'}{album.year ? ` • ${album.year}` : ''}</span>
        <SourceBadge source={album.source} />
      </div>
      <div className="album-card-actions album-card-actions--compact" onClick={(event) => event.stopPropagation()}>
        <button className="primary" onClick={() => player.run(() => api.playAlbum(album), 'play')}>▶</button>
        <details className="album-card-menu">
          <summary className="icon-button compact-action album-more-button" aria-label={`More options for ${album.title}`} title="More options">⋯</summary>
          <div className="album-card-menu-popover">
            <button type="button" onClick={() => player.run(() => api.queueAlbum(album))}>Queue</button>
            {canAdd && album.source !== 'subsonic' ? <button type="button" onClick={() => void addToSubsonic()}>Add to Subsonic</button> : null}
          </div>
        </details>
      </div>
    </article>
  )
}

function ArtistCard({ artist, searchReturn }: { artist: SearchArtist; searchReturn: SearchReturnState }) {
  const browseId = artistBrowseId(artist)
  return (
    <Link className="artist-result-card" to={browseId ? `/artists/${encodeURIComponent(browseId)}` : '#'} state={browseId ? { searchReturn } : undefined}>
      <Artwork src={resultArtwork(artist)} alt={artist.name} size="md" />
      <div>
        <strong>{artist.name}</strong>
        <span>{artist.subscriber_count || artist.monthly_listeners || 'Artist'}</span>
      </div>
    </Link>
  )
}

export function SearchPage() {
  const player = useOutletContext<PlayerContext>()
  const location = useLocation()
  const restoredSearch = (location.state as SearchRouteState | null)?.searchReturn
  const [query, setQuery] = useState(restoredSearch?.query ?? '')
  const [mode, setMode] = useState<SearchMode>(restoredSearch?.mode ?? 'hybrid')
  const [results, setResults] = useState<SearchResponse>(restoredSearch?.results ?? { mode: restoredSearch?.mode ?? 'hybrid', songs: [], albums: [] })
  const [artists, setArtists] = useState<SearchArtist[]>(restoredSearch?.artists ?? [])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  const selectedMode = SEARCH_MODES.find((item) => item.id === mode) ?? SEARCH_MODES[0]
  const featuredResult = useMemo(() => topResult(results), [results])
  const hasResults = results.songs.length > 0 || results.albums.length > 0 || artists.length > 0
  const currentSearchReturn: SearchReturnState = { query, mode, results, artists }

  async function runSearch(nextMode = mode, nextQuery = query.trim()) {
    if (!nextQuery) return
    setLoading(true)
    setError('')
    setStatus('')
    try {
      const [searchResult, artistResult] = await Promise.all([
        api.search(nextQuery, nextMode),
        nextMode === 'subsonic' ? Promise.resolve({ artists: [] }) : api.searchArtists(nextQuery),
      ])
      setResults(searchResult)
      setArtists(artistResult.artists)
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
    if (query.trim()) await runSearch(nextMode)
  }

  function clearSearch() {
    setQuery('')
    setResults({ mode, songs: [], albums: [] })
    setArtists([])
    setError('')
    setStatus('')
  }

  return (
    <div className="search-redesign">
      <section className="search-hero">
        <h1>Search</h1>
        <p>Find music across your Subsonic library and YTMusic discovery without exposing either service to the browser.</p>
        <form className="search-command" onSubmit={search}>
          <span aria-hidden="true">⌕</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search songs, albums, artists..." autoFocus />
          {query ? <button type="button" className="search-clear" onClick={clearSearch} aria-label="Clear search">×</button> : null}
          <button className="primary" disabled={loading || !query.trim()}>{loading ? 'Searching...' : 'Search'}</button>
        </form>
        <div className="search-mode-row">
          <div className="search-tabs" role="tablist" aria-label="Search mode">
            {SEARCH_MODES.map((item) => <button key={item.id} type="button" className={`tab-button ${mode === item.id ? 'active' : ''}`} onClick={() => void selectMode(item.id)}>{item.label}</button>)}
          </div>
          <p>{selectedMode.description}</p>
        </div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}
      {featuredResult ? <TopResultCard result={featuredResult} player={player} onStatus={setStatus} searchReturn={currentSearchReturn} /> : null}

      {hasResults ? (
        <div className="search-result-counts" aria-label="Search result counts">
          <div><strong>{results.songs.length}</strong><span>Songs</span></div>
          <div><strong>{results.albums.length}</strong><span>Albums</span></div>
          <div><strong>{artists.length}</strong><span>Artists</span></div>
          <div><strong>{results.songs.filter((song) => song.source === 'subsonic').length + results.albums.filter((album) => album.source === 'subsonic').length}</strong><span>Library matches</span></div>
        </div>
      ) : null}

      <section className="search-section">
        <div className="search-section-heading"><span>Artists</span>{artists.length ? <small>{artists.length} results</small> : null}</div>
        {artists.length ? <div className="artist-result-grid">{artists.map((artist) => <ArtistCard key={artist.browse_id} artist={artist} searchReturn={currentSearchReturn} />)}</div> : <p className="muted search-empty">{loading ? 'Searching artists…' : query && mode !== 'subsonic' ? 'No artist results.' : 'YTMusic artist results appear here.'}</p>}
      </section>

      <section className="search-section">
        <div className="search-section-heading"><span>Songs</span>{results.songs.length ? <small>{results.songs.length} results</small> : null}</div>
        {results.songs.length ? <div className="search-song-grid">{results.songs.map((song, index) => <SongRow key={`${song.source}-${song.title}-${song.artist}-${index}`} song={song} player={player} onStatus={setStatus} />)}</div> : <p className="muted search-empty">{loading ? 'Searching songs…' : query ? 'No song results.' : 'Search to see songs here.'}</p>}
      </section>

      <section className="search-section">
        <div className="search-section-heading"><span>Albums</span>{results.albums.length ? <small>{results.albums.length} results</small> : null}</div>
        {results.albums.length ? <div className="search-album-strip">{results.albums.map((album, index) => <AlbumCard key={`${album.source}-${album.title}-${album.artist}-${index}`} album={album} player={player} onStatus={setStatus} searchReturn={currentSearchReturn} />)}</div> : <p className="muted search-empty">{loading ? 'Searching albums…' : query ? 'No album results.' : 'Search to see albums here.'}</p>}
      </section>
    </div>
  )
}
