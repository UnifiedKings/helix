import { FormEvent, useEffect, useMemo, useState } from 'react'
import type { CSSProperties, KeyboardEvent } from 'react'
import { Link, useLocation, useNavigate, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { Capabilities, SearchAlbum, SearchArtist, SearchMode, SearchResponse, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
import { ArtistLink } from '../components/ArtistLink'
import { AlbumLink } from '../components/AlbumLink'
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

type ResultSection = 'songs' | 'albums' | 'artists'

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

function albumDetailPath(album: SearchAlbum) {
  const albumId = albumBrowseId(album)
  if (!albumId) return ''
  const path = `/albums/${encodeURIComponent(albumId)}`
  return album.source === 'subsonic' ? `${path}?source=subsonic` : path
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

function TopResultCard({ result, player, onStatus, searchReturn, canImportToSubsonic }: { result: NonNullable<ReturnType<typeof topResult>>; player: PlayerContext; onStatus: (message: string) => void; searchReturn: SearchReturnState; canImportToSubsonic: boolean }) {
  const item = result.item
  const title = item.title
  const artwork = resultArtwork(item)
  const isYt = item.source === 'ytmusic' || (!item.source && result.kind === 'album')
  const topResultStyle = artwork ? ({ '--search-feature-art': `url(${JSON.stringify(artwork)})` } as CSSProperties) : undefined
  const [subsonicQueued, setSubsonicQueued] = useState(false)

  useEffect(() => {
    setSubsonicQueued(false)
  }, [result.kind, item.title, 'artist' in item ? item.artist : '', item.source])

  async function addToSubsonic() {
    if (!isYt || subsonicQueued) return
    setSubsonicQueued(true)
    try {
      if (result.kind === 'album') {
        await api.addAlbumToSubsonic(result.item)
        onStatus(`Queued album for Subsonic import: ${result.item.title}`)
      } else {
        await api.addSongToSubsonic(result.item)
        onStatus(`Queued track for Subsonic import: ${result.item.title}`)
      }
    } catch {
      setSubsonicQueued(false)
      onStatus(`Could not queue ${result.kind} for Subsonic import: ${result.item.title}`)
    }
  }

  return (
    <section className="search-top-result">
      <div className="search-section-heading"><span>Top result</span></div>
      <article className="top-result-card" style={topResultStyle}>
        <Artwork src={artwork} alt={title} size="lg" />
        <div className="top-result-copy">
          <span className="result-kind">{result.kind}</span>
          <h2>{result.kind === 'album' ? <AlbumLink album={result.item.title} artist={result.item.artist} albumId={albumBrowseId(result.item)} source={result.item.source} /> : title}</h2>
          <p>{result.kind === 'album'
            ? <><ArtistLink artist={result.item.artist ?? 'Unknown artist'} />{result.item.year ? ` • ${result.item.year}` : ''}</>
            : <><ArtistLink artist={result.item.artist} />{result.item.album ? <> • <AlbumLink album={result.item.album} artist={result.item.artist} source={result.item.source} /></> : null}</>}</p>
          <div className="top-result-library-state"><SourceBadge source={item.source} /></div>
          <div className="top-result-actions">
            <button className="primary" onClick={() => result.kind === 'album' ? player.run(() => api.playAlbum(result.item), 'play') : player.run(() => api.playSong(result.item), 'play')}>▶ Play</button>
            <button onClick={() => result.kind === 'album' ? player.run(() => api.queueAlbum(result.item)) : player.run(() => api.queueSong(result.item))}>＋ Add to Queue</button>
            {isYt && canImportToSubsonic ? <button disabled={subsonicQueued} onClick={() => void addToSubsonic()}>S+ {subsonicQueued ? 'Queued' : 'Add to Subsonic'}</button> : null}
            {result.kind === 'album' && albumDetailPath(result.item) ? <Link className="button-link" to={albumDetailPath(result.item)} state={{ searchReturn }}>Open album</Link> : null}
          </div>
        </div>
      </article>
    </section>
  )
}

function SongRow({ song, player, onStatus, canImportToSubsonic }: { song: SearchSong; player: PlayerContext; onStatus: (message: string) => void; canImportToSubsonic: boolean }) {
  const duration = durationLabel(song)
  const canAdd = song.source === 'ytmusic' || Boolean(song.yt_video_id || song.video_id || song.videoId)
  const [subsonicQueued, setSubsonicQueued] = useState(false)

  async function addToSubsonic() {
    if (subsonicQueued) return
    setSubsonicQueued(true)
    try {
      await api.addSongToSubsonic(song)
      onStatus(`Queued track for Subsonic import: ${song.title}`)
    } catch {
      setSubsonicQueued(false)
      onStatus(`Could not queue track for Subsonic import: ${song.title}`)
    }
  }

  return (
    <article className="search-song-row search-shared-result-row">
      <Artwork src={resultArtwork(song)} alt={song.title} size="sm" />
      <div className="song-title-cell">
        <strong>{song.title}</strong>
        <div className="song-meta-line">
          <ArtistLink artist={song.artist} />
          {song.album ? <span className="song-meta-album">• <AlbumLink album={song.album} artist={song.artist} source={song.source} /></span> : null}
        </div>
      </div>
      <SourceBadge source={song.source} />
      <span className="song-duration">{duration}</span>
      <div className="search-row-actions">
        <button className="search-row-icon" aria-label={`Play ${song.title}`} data-tooltip="Play" title="Play" onClick={() => player.run(() => api.playSong(song), 'play')}>▶</button>
        <button className="search-row-icon search-queue-icon" aria-label={`Add ${song.title} to queue`} data-tooltip="Add to queue" title="Add to queue" onClick={() => player.run(() => api.queueSong(song))}>＋</button>
        {canAdd && canImportToSubsonic && song.source !== 'subsonic' ? <button className="search-row-icon search-library-icon" disabled={subsonicQueued} aria-label={subsonicQueued ? `${song.title} queued for Subsonic import` : `Add ${song.title} to Subsonic`} data-tooltip={subsonicQueued ? 'Queued for Subsonic' : 'Add to Subsonic'} title={subsonicQueued ? 'Queued for Subsonic' : 'Add to Subsonic'} onClick={() => void addToSubsonic()}>S+</button> : <span className="search-row-icon-spacer" aria-hidden="true" />}
      </div>
    </article>
  )
}

function AlbumCard({ album, player, onStatus, searchReturn, canImportToSubsonic }: { album: SearchAlbum; player: PlayerContext; onStatus: (message: string) => void; searchReturn: SearchReturnState; canImportToSubsonic: boolean }) {
  const navigate = useNavigate()
  const browseId = albumBrowseId(album)
  const albumPath = browseId ? albumDetailPath(album) : ''
  const canAdd = album.source === 'ytmusic' || Boolean(browseId)
  const [subsonicQueued, setSubsonicQueued] = useState(false)

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
    if (subsonicQueued) return
    setSubsonicQueued(true)
    try {
      await api.addAlbumToSubsonic(album)
      onStatus(`Queued album for Subsonic import: ${album.title}`)
    } catch {
      setSubsonicQueued(false)
      onStatus(`Could not queue album for Subsonic import: ${album.title}`)
    }
  }

  return (
    <article
      className={`search-album-card search-shared-result-row ${albumPath ? 'search-album-card-clickable' : ''}`}
      onClick={openAlbum}
      onKeyDown={handleAlbumKeyDown}
      role={albumPath ? 'button' : undefined}
      tabIndex={albumPath ? 0 : undefined}
      aria-label={albumPath ? `Open album ${album.title}` : undefined}
    >
      <Artwork src={resultArtwork(album)} alt={album.title} size="lg" />
      <div className="album-card-body">
        <strong><AlbumLink album={album.title} artist={album.artist} albumId={albumBrowseId(album)} source={album.source} /></strong>
        <span><ArtistLink artist={album.artist ?? 'Unknown artist'} />{album.year ? ` • ${album.year}` : ''}</span>
      </div>
      <SourceBadge source={album.source} />
      <span className="song-duration album-duration-spacer" aria-hidden="true" />
      <div className="search-row-actions" onClick={(event) => event.stopPropagation()}>
        <button className="search-row-icon" aria-label={`Play ${album.title}`} data-tooltip="Play" title="Play" onClick={() => player.run(() => api.playAlbum(album), 'play')}>▶</button>
        <button className="search-row-icon search-queue-icon" aria-label={`Add ${album.title} to queue`} data-tooltip="Add to queue" title="Add to queue" onClick={() => player.run(() => api.queueAlbum(album))}>＋</button>
        {canAdd && canImportToSubsonic && album.source !== 'subsonic' ? (
          <button
            className="search-row-icon search-library-icon"
            disabled={subsonicQueued}
            aria-label={subsonicQueued ? `${album.title} queued for Subsonic import` : `Add ${album.title} to Subsonic`}
            data-tooltip={subsonicQueued ? 'Queued for Subsonic' : 'Add to Subsonic'}
            title={subsonicQueued ? 'Queued for Subsonic' : 'Add to Subsonic'}
            onClick={() => void addToSubsonic()}
          >
            S+
          </button>
        ) : <span className="search-row-icon-spacer" aria-hidden="true" />}
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
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [activeResultSection, setActiveResultSection] = useState<ResultSection>('songs')

  useEffect(() => {
    if (restoredSearch) return
    let cancelled = false
    void api.userSettings().then((prefs) => {
      if (cancelled) return
      setMode(prefs.settings.search_default_mode)
      setActiveResultSection(prefs.settings.search_default_tab)
    }).catch(() => undefined)
    return () => { cancelled = true }
  }, [])

  const subsonicConfigured = capabilities?.subsonic_configured !== false
  const availableSearchModes = useMemo(() => subsonicConfigured ? SEARCH_MODES : SEARCH_MODES.filter((item) => item.id === 'ytmusic'), [subsonicConfigured])
  const selectedMode = availableSearchModes.find((item) => item.id === mode) ?? availableSearchModes[0]
  const featuredResult = useMemo(() => topResult(results), [results])
  const hasResults = results.songs.length > 0 || results.albums.length > 0 || artists.length > 0
  const currentSearchReturn: SearchReturnState = { query, mode, results, artists }

  useEffect(() => {
    api.capabilities()
      .then((payload) => {
        setCapabilities(payload)
        if (!payload.subsonic_configured && mode !== 'ytmusic') setMode('ytmusic')
      })
      .catch(() => setCapabilities({ subsonic_configured: true, features: { library_search: true, subsonic_import: true, library_only_stations: true, subsonic_playback: true, ytmusic_discovery: true, ytmusic_playback: true, lobbies: true } }))
  }, [])

  async function runSearch(nextMode = mode, nextQuery = query.trim()) {
    if (!nextQuery) return
    if (!subsonicConfigured && nextMode !== 'ytmusic') nextMode = 'ytmusic'
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
      <header className="search-page-header">
        <h1>Search</h1>
        <form className="search-command" onSubmit={search}>
          <span aria-hidden="true" className="search-command-icon">⌕</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search songs, albums, artists..." autoFocus />
          {query ? <button type="button" className="search-clear" onClick={clearSearch} aria-label="Clear search">×</button> : null}
          <button className="primary" disabled={loading || !query.trim()}>{loading ? 'Searching...' : 'Search'}</button>
        </form>
        <div className="search-mode-row">
          <div className="search-tabs" role="tablist" aria-label="Search mode">
            {availableSearchModes.map((item) => <button key={item.id} type="button" className={`tab-button ${mode === item.id ? 'active' : ''}`} onClick={() => void selectMode(item.id)}>{item.label}</button>)}
          </div>
          <p>{selectedMode.description}</p>
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}
      {featuredResult ? <TopResultCard result={featuredResult} player={player} onStatus={setStatus} searchReturn={currentSearchReturn} canImportToSubsonic={Boolean(capabilities?.features.subsonic_import)} /> : null}

      {hasResults ? (
        <nav className="search-result-nav" aria-label="Search result sections" role="tablist">
          <button type="button" role="tab" aria-selected={activeResultSection === 'songs'} className={activeResultSection === 'songs' ? 'active' : ''} onClick={() => setActiveResultSection('songs')}>Songs <span>{results.songs.length}</span></button>
          <button type="button" role="tab" aria-selected={activeResultSection === 'albums'} className={activeResultSection === 'albums' ? 'active' : ''} onClick={() => setActiveResultSection('albums')}>Albums <span>{results.albums.length}</span></button>
          <button type="button" role="tab" aria-selected={activeResultSection === 'artists'} className={activeResultSection === 'artists' ? 'active' : ''} onClick={() => setActiveResultSection('artists')}>Artists <span>{artists.length}</span></button>
        </nav>
      ) : null}

      {activeResultSection === 'songs' ? (
        <section className="search-section search-songs-section search-table-section" id="search-songs" role="tabpanel">
          {results.songs.length ? (
            <>
              <div className="search-section-heading search-column-heading">
                <span className="search-heading-title">Songs</span>
                <span className="search-heading-source">Source</span>
                <span className="search-heading-time">Time</span>
                <span className="search-heading-actions">Actions</span>
              </div>
              <div className="search-song-grid">
                {results.songs.map((song, index) => <SongRow key={`${song.source}-${song.title}-${song.artist}-${index}`} song={song} player={player} onStatus={setStatus} canImportToSubsonic={Boolean(capabilities?.features.subsonic_import)} />)}
              </div>
            </>
          ) : (
            <p className="muted search-empty">{loading ? 'Searching songs…' : query ? 'No song results.' : 'Search to see songs here.'}</p>
          )}
        </section>
      ) : null}

      {activeResultSection === 'artists' ? (
        <section className="search-section" id="search-artists" role="tabpanel">
          <div className="search-section-heading"><span>Artists</span>{artists.length ? <small>{artists.length} results</small> : null}</div>
          {artists.length ? <div className="artist-result-grid">{artists.map((artist) => <ArtistCard key={artist.browse_id} artist={artist} searchReturn={currentSearchReturn} />)}</div> : <p className="muted search-empty">{loading ? 'Searching artists…' : query && mode !== 'subsonic' ? 'No artist results.' : 'YTMusic artist results appear here.'}</p>}
        </section>
      ) : null}

      {activeResultSection === 'albums' ? (
        <section className="search-section search-albums-section search-table-section" id="search-albums" role="tabpanel">
          {results.albums.length ? (
            <div className="search-section-heading search-column-heading">
              <span className="search-heading-title">Albums</span>
              <span className="search-heading-source">Source</span>
              <span className="search-heading-time" aria-hidden="true" />
              <span className="search-heading-actions">Actions</span>
            </div>
          ) : (
            <div className="search-section-heading"><span>Albums</span></div>
          )}
          {results.albums.length ? <div className="search-album-strip">{results.albums.map((album, index) => <AlbumCard key={`${album.source}-${album.title}-${album.artist}-${index}`} album={album} player={player} onStatus={setStatus} searchReturn={currentSearchReturn} canImportToSubsonic={Boolean(capabilities?.features.subsonic_import)} />)}</div> : <p className="muted search-empty">{loading ? 'Searching albums…' : query ? 'No album results.' : 'Search to see albums here.'}</p>}
        </section>
      ) : null}
    </div>
  )
}
