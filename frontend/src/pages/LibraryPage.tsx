import { FormEvent, useEffect, useMemo, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { SearchAlbum, SearchArtist, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
import { AlbumLink } from '../components/AlbumLink'
import { ArtistLink } from '../components/ArtistLink'
import type { usePlayer } from '../hooks/usePlayer'
import '../styles/library.css'

type PlayerContext = ReturnType<typeof usePlayer>

type LibraryTab = 'albums' | 'artists' | 'songs'
type SongSort = 'random' | 'starred'

const ALBUM_SORTS: Array<{ id: string; label: string }> = [
  { id: 'newest', label: 'Newest' },
  { id: 'recent', label: 'Recently added' },
  { id: 'frequent', label: 'Most played' },
  { id: 'random', label: 'Random' },
  { id: 'starred', label: 'Favorites' },
  { id: 'alphabeticalByName', label: 'A–Z' },
]

const SONG_SORTS: Array<{ id: SongSort; label: string }> = [
  { id: 'random', label: 'Random' },
  { id: 'starred', label: 'Favorites' },
]

function albumBrowseId(album: SearchAlbum) {
  return album.yt_browse_id || album.browse_id || album.browseId || album.subsonic_album_id || ''
}

function albumDetailPath(album: SearchAlbum) {
  const albumId = albumBrowseId(album)
  if (!albumId) return ''
  return `/albums/${encodeURIComponent(albumId)}?source=subsonic`
}

function artistPath(artist: SearchArtist) {
  const id = artist.browse_id || artist.artist_id || ''
  return id ? `/artists/${encodeURIComponent(id)}?source=subsonic` : ''
}

function formatDuration(song: SearchSong) {
  if (song.duration_seconds) return `${Math.floor(song.duration_seconds / 60)}:${String(Math.round(song.duration_seconds % 60)).padStart(2, '0')}`
  if (song.duration_ms) return `${Math.floor(song.duration_ms / 60000)}:${String(Math.round((song.duration_ms % 60000) / 1000)).padStart(2, '0')}`
  return ''
}

function SongRow({ song, player }: { song: SearchSong; player: PlayerContext }) {
  const duration = formatDuration(song)
  const artwork = song.art_url || song.thumbnail_url || ''
  return (
    <article className="library-song-row">
      <Artwork src={artwork} alt={song.title} size="sm" />
      <div className="library-song-copy">
        <strong>{song.title}</strong>
        <div className="library-song-meta">
          <ArtistLink artist={song.artist} />
          {song.album ? <span>• <AlbumLink album={song.album} artist={song.artist} source="subsonic" /></span> : null}
        </div>
      </div>
      <span className="library-song-duration">{duration}</span>
      <div className="library-row-actions">
        <button className="library-row-icon" aria-label={`Play ${song.title}`} data-tooltip="Play" title="Play" onClick={() => player.run(() => api.playSong(song), 'play')}>▶</button>
        <button className="library-row-icon library-queue-icon" aria-label={`Add ${song.title} to queue`} data-tooltip="Add to queue" title="Add to queue" onClick={() => player.run(() => api.queueSong(song))}>＋</button>
      </div>
    </article>
  )
}

function AlbumTile({ album, player }: { album: SearchAlbum; player: PlayerContext }) {
  const navigate = useNavigate()
  const path = albumDetailPath(album)
  const artwork = album.art_url || album.thumbnail_url || ''

  function openAlbum() {
    if (path) navigate(path)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!path) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openAlbum()
    }
  }

  return (
    <article
      className={`library-album-card${path ? ' library-album-card-clickable' : ''}`}
      onClick={openAlbum}
      onKeyDown={handleKeyDown}
      role={path ? 'button' : undefined}
      tabIndex={path ? 0 : undefined}
      aria-label={path ? `Open album ${album.title}` : undefined}
    >
      <Artwork src={artwork} alt={album.title} size="lg" />
      <div className="library-album-card-body">
        <strong><AlbumLink album={album.title} artist={album.artist} albumId={albumBrowseId(album)} source="subsonic" /></strong>
        <span><ArtistLink artist={album.artist ?? 'Unknown artist'} />{album.year ? ` • ${album.year}` : ''}</span>
      </div>
      <div className="library-row-actions" onClick={(event) => event.stopPropagation()}>
        <button className="library-row-icon" aria-label={`Play ${album.title}`} data-tooltip="Play" title="Play" onClick={() => player.run(() => api.playAlbum(album), 'play')}>▶</button>
        <button className="library-row-icon library-queue-icon" aria-label={`Add ${album.title} to queue`} data-tooltip="Add to queue" title="Add to queue" onClick={() => player.run(() => api.queueAlbum(album))}>＋</button>
      </div>
    </article>
  )
}

function ArtistTile({ artist }: { artist: SearchArtist }) {
  const path = artistPath(artist)
  const content = (
    <>
      <Artwork src={artist.art_url || artist.thumbnail_url} alt={artist.name} size="md" />
      <div className="library-artist-copy">
        <strong>{artist.name}</strong>
        <span>{artist.album_count ? `${artist.album_count} album${artist.album_count === 1 ? '' : 's'}` : 'Artist'}</span>
      </div>
    </>
  )
  return path ? (
    <Link className="library-artist-tile" to={path}>{content}</Link>
  ) : (
    <div className="library-artist-tile">{content}</div>
  )
}

function groupArtistsByLetter(artists: SearchArtist[]) {
  const groups = new Map<string, SearchArtist[]>()
  for (const artist of artists) {
    const initial = (artist.name.trim().charAt(0) || '#').toUpperCase()
    const letter = /[A-Z0-9]/.test(initial) ? initial : '#'
    const rows = groups.get(letter) ?? []
    rows.push(artist)
    groups.set(letter, rows)
  }
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))
}

export function LibraryPage() {
  const player = useOutletContext<PlayerContext>()
  const [tab, setTab] = useState<LibraryTab>('albums')
  const [configured, setConfigured] = useState<boolean | null>(null)

  const [albumSort, setAlbumSort] = useState('newest')
  const [albums, setAlbums] = useState<{ items: SearchAlbum[]; offset: number; hasMore: boolean }>({ items: [], offset: 0, hasMore: false })
  const [albumsLoading, setAlbumsLoading] = useState(false)

  const [artists, setArtists] = useState<SearchArtist[]>([])
  const [artistsLoading, setArtistsLoading] = useState(false)
  const [artistsLoaded, setArtistsLoaded] = useState(false)

  const [songSort, setSongSort] = useState<SongSort>('random')
  const [songs, setSongs] = useState<SearchSong[]>([])
  const [songsLoading, setSongsLoading] = useState(false)

  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    api.capabilities().then((payload) => {
      if (cancelled) return
      setConfigured(Boolean(payload.subsonic_configured))
      if (payload.subsonic_configured) void loadAlbums('newest', true)
    }).catch(() => {
      if (!cancelled) setConfigured(false)
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadAlbums(type: string, reset = false) {
    const offset = reset ? 0 : albums.offset
    setAlbumsLoading(true)
    setError('')
    try {
      const res = await api.subsonicLibraryAlbums(type, offset, 24)
      setAlbums((current) => ({
        items: reset ? res.albums : [...current.items, ...res.albums],
        offset: offset + res.albums.length,
        hasMore: res.has_more,
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load albums')
    } finally {
      setAlbumsLoading(false)
    }
  }

  async function selectAlbumSort(type: string) {
    if (type === albumSort) return
    setAlbumSort(type)
    await loadAlbums(type, true)
  }

  async function loadArtists() {
    if (artistsLoaded) return
    setArtistsLoading(true)
    setError('')
    try {
      const res = await api.subsonicLibraryArtists()
      setArtists(res.artists)
      setArtistsLoaded(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load artists')
    } finally {
      setArtistsLoading(false)
    }
  }

  async function loadSongs(type: SongSort) {
    setSongsLoading(true)
    setSongs([])
    setError('')
    try {
      const res = await api.subsonicLibrarySongs(type, 100)
      setSongs(res.songs)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load songs')
    } finally {
      setSongsLoading(false)
    }
  }

  function switchTab(next: LibraryTab) {
    setTab(next)
    setError('')
    if (next === 'artists' && !artistsLoaded && configured) void loadArtists()
    if (next === 'songs' && songs.length === 0 && configured) void loadSongs(songSort)
  }

  async function shuffleSongs(event: FormEvent) {
    event.preventDefault()
    await loadSongs('random')
  }

  const artistGroups = useMemo(() => groupArtistsByLetter(artists), [artists])

  if (configured === false) {
    return (
      <div className="page-stack library-page">
        <header className="library-page-header">
          <h1>Library</h1>
          <p>Browse music available in your connected Subsonic server.</p>
        </header>
        <section className="library-unconfigured">
          <p className="muted">Subsonic is not configured, so there is no library to browse yet.</p>
          <Link className="button-link" to="/admin/settings">Open Subsonic settings</Link>
        </section>
      </div>
    )
  }

  return (
    <div className="page-stack library-page">
      <header className="library-page-header">
        <h1>Library</h1>
        <p>Browse music available in your connected Subsonic server.</p>
        <nav className="library-tabs" role="tablist" aria-label="Library sections">
          <button type="button" role="tab" aria-selected={tab === 'albums'} className={tab === 'albums' ? 'active' : ''} onClick={() => switchTab('albums')}>Albums</button>
          <button type="button" role="tab" aria-selected={tab === 'artists'} className={tab === 'artists' ? 'active' : ''} onClick={() => switchTab('artists')}>Artists</button>
          <button type="button" role="tab" aria-selected={tab === 'songs'} className={tab === 'songs' ? 'active' : ''} onClick={() => switchTab('songs')}>Songs</button>
        </nav>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      {tab === 'albums' ? (
        <section role="tabpanel" aria-label="Albums">
          <div className="library-sort-row">
            {ALBUM_SORTS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`library-sort-chip${albumSort === item.id ? ' active' : ''}`}
                onClick={() => void selectAlbumSort(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          {albums.items.length ? (
            <>
              <div className="library-album-grid">
                {albums.items.map((album, index) => <AlbumTile key={`${album.subsonic_album_id || album.title}-${index}`} album={album} player={player} />)}
              </div>
              {albums.hasMore ? (
                <div className="library-load-more">
                  <button className="primary" disabled={albumsLoading} onClick={() => void loadAlbums(albumSort, false)}>{albumsLoading ? 'Loading…' : 'Load more'}</button>
                </div>
              ) : null}
            </>
          ) : (
            <p className="muted library-empty">{albumsLoading ? 'Loading albums…' : 'No albums found.'}</p>
          )}
        </section>
      ) : null}

      {tab === 'artists' ? (
        <section role="tabpanel" aria-label="Artists">
          {artistGroups.length ? (
            <>
              <nav className="library-letter-bar" aria-label="Jump to letter">
                {artistGroups.map(([letter]) => (
                  <a key={letter} href={`#library-letter-${letter}`}>{letter}</a>
                ))}
              </nav>
              {artistGroups.map(([letter, rows]) => (
                <div className="library-letter-group" key={letter}>
                  <h2 id={`library-letter-${letter}`} className="library-letter-heading">{letter}</h2>
                  <div className="library-artist-grid">
                    {rows.map((artist) => <ArtistTile key={artist.browse_id || artist.name} artist={artist} />)}
                  </div>
                </div>
              ))}
            </>
          ) : (
            <p className="muted library-empty">{artistsLoading ? 'Loading artists…' : 'No artists found.'}</p>
          )}
        </section>
      ) : null}

      {tab === 'songs' ? (
        <section role="tabpanel" aria-label="Songs">
          <div className="library-sort-row">
            {SONG_SORTS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`library-sort-chip${songSort === item.id ? ' active' : ''}`}
                onClick={() => void (async () => { setSongSort(item.id); await loadSongs(item.id) })()}
              >
                {item.label}
              </button>
            ))}
            {songSort === 'random' && songs.length > 0 ? (
              <button type="button" className="library-shuffle-button" onClick={shuffleSongs} disabled={songsLoading}>Shuffle again</button>
            ) : null}
          </div>
          {songs.length ? (
            <div className="library-song-grid">
              {songs.map((song, index) => <SongRow key={`${song.subsonic_song_id || song.title}-${index}`} song={song} player={player} />)}
            </div>
          ) : (
            <p className="muted library-empty">{songsLoading ? 'Loading songs…' : songSort === 'starred' ? 'No starred songs yet.' : 'No songs found.'}</p>
          )}
        </section>
      ) : null}
    </div>
  )
}