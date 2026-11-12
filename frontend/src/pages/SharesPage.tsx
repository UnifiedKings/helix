import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { Playlist, SearchAlbum, SearchMode, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
import '../styles/shares.css'
import '../styles/shares-management.css'

type Kind = 'track' | 'album' | 'playlist'
type Candidate = {
  type: Kind
  id: string
  source: 'subsonic' | 'ytmusic' | 'helix'
  title: string
  artist: string
  album: string
  art: string
  duration_ms: number
}
type Share = {
  token: string
  title: string
  artist?: string
  art_url?: string
  resource_type: string
  expires_at?: string | null
  expired: boolean
  url: string
}
type Config = { expiration_days: number[]; default_expiration_days: number }

const modes: { id: SearchMode; label: string }[] = [
  { id: 'hybrid', label: 'All' },
  { id: 'subsonic', label: 'Library' },
  { id: 'ytmusic', label: 'YTMusic' },
]

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail || `Request failed (${response.status})`)
  return body as T
}

function fromSong(song: SearchSong): Candidate | null {
  const library = Boolean(song.subsonic_song_id)
  const id = library ? song.subsonic_song_id : (song.yt_video_id || song.video_id || song.videoId)
  if (!id) return null
  return {
    type: 'track', id, source: library ? 'subsonic' : 'ytmusic',
    title: song.title, artist: song.artist, album: song.album || '',
    art: song.art_url || song.thumbnail_url || '',
    duration_ms: song.duration_ms || (song.duration_seconds || 0) * 1000,
  }
}

function fromAlbum(album: SearchAlbum): Candidate | null {
  const library = album.source === 'subsonic' || Boolean(album.subsonic_album_id)
  const id = library
    ? (album.subsonic_album_id || album.browse_id)
    : (album.yt_browse_id || album.browse_id || album.browseId)
  if (!id) return null
  return {
    type: 'album', id, source: library ? 'subsonic' : 'ytmusic',
    title: album.title, artist: album.artist || '', album: '',
    art: album.art_url || album.thumbnail_url || '', duration_ms: 0,
  }
}

function urlFor(share: Share) {
  return new URL(share.url, window.location.origin).toString()
}

export function SharesPage() {
  const [kind, setKind] = useState<Kind>('track')
  const [mode, setMode] = useState<SearchMode>('hybrid')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Candidate[]>([])
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [shares, setShares] = useState<Share[]>([])
  const [selected, setSelected] = useState<Candidate | null>(null)
  const [config, setConfig] = useState<Config>({ expiration_days: [1, 7, 30, 365, 0], default_expiration_days: 30 })
  const [days, setDays] = useState(30)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [newUrl, setNewUrl] = useState('')
  const [searched, setSearched] = useState(false)
  const searchSequence = useRef(0)

  async function loadShares() {
    setShares(await request<Share[]>('/api/shares'))
  }

  useEffect(() => {
    void loadShares().catch(err => setError(String(err.message)))
    void request<Config>('/api/shares/config').then(next => {
      setConfig(next)
      setDays(next.default_expiration_days)
    }).catch(() => {})
    void api.playlists().then(setPlaylists).catch(() => {})
  }, [])

  async function runSearch(nextMode: SearchMode, nextKind: Kind, nextQuery: string) {
    const term = nextQuery.trim()
    if (!term || nextKind === 'playlist') return
    const sequence = ++searchSequence.current
    setBusy(true)
    setError('')
    setSelected(null)
    setNewUrl('')
    try {
      const response = await api.search(term, nextMode)
      if (sequence !== searchSequence.current) return
      setResults(nextKind === 'track'
        ? response.songs.map(fromSong).filter((item): item is Candidate => item !== null)
        : response.albums.map(fromAlbum).filter((item): item is Candidate => item !== null))
      setSearched(true)
    } catch (err) {
      if (sequence === searchSequence.current) setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      if (sequence === searchSequence.current) setBusy(false)
    }
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault()
    void runSearch(mode, kind, query)
  }

  function changeKind(next: Kind) {
    searchSequence.current++
    setKind(next)
    setSelected(null)
    setNewUrl('')
    setResults([])
    setSearched(false)
    setBusy(false)
  }

  function changeMode(next: SearchMode) {
    setMode(next)
    setResults([])
    setSelected(null)
    setSearched(false)
    if (query.trim()) void runSearch(next, kind, query)
  }

  async function createShare() {
    if (!selected || busy) return
    setBusy(true)
    setError('')
    try {
      const share = await request<Share>('/api/shares', {
        method: 'POST',
        body: JSON.stringify({
          resource_type: selected.type,
          resource_id: selected.id,
          resource_source: selected.source,
          title: selected.title,
          artist: selected.artist,
          album: selected.album,
          art_url: selected.art,
          duration_ms: selected.duration_ms,
          expiration_days: days,
        }),
      })
      const url = urlFor(share)
      setNewUrl(url)
      setSelected(null)
      await loadShares()
      try { await navigator.clipboard.writeText(url) } catch { /* URL remains visible */ }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create share')
    } finally {
      setBusy(false)
    }
  }

  async function revoke(share: Share) {
    if (!window.confirm(`Revoke the link for "${share.title}"?`)) return
    try {
      await request(`/api/shares/${encodeURIComponent(share.token)}`, { method: 'DELETE' })
      await loadShares()
      if (newUrl === urlFor(share)) setNewUrl('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not revoke link')
    }
  }

  async function copy(url: string) {
    try { await navigator.clipboard.writeText(url) }
    catch { setError('Could not copy the link. Please copy it manually.') }
  }

  const candidates: Candidate[] = kind === 'playlist'
    ? playlists.map(playlist => ({
        type: 'playlist', id: playlist.id, source: 'helix',
        title: playlist.name, artist: `${playlist.track_count || 0} tracks`,
        album: '', art: playlist.cover_url || '', duration_ms: 0,
      }))
    : results

  return (
    <div className="page-stack shares-page shares-management">
      <header className="shares-management-header">
        <h1>Shares</h1>
        <p className="muted">Create links for music and manage access to your shared collections.</p>
      </header>

      {error && <div className="error-banner" role="alert">{error}</div>}

      <section className="share-create-card">
        <h2>Create a share</h2>
        <div className="share-tabs" role="group" aria-label="Music type">
          {(['track', 'album', 'playlist'] as const).map(item => (
            <button type="button" key={item} className={kind === item ? 'active' : ''}
              onClick={() => changeKind(item)}>{item[0].toUpperCase() + item.slice(1)}</button>
          ))}
        </div>

        {kind !== 'playlist' && (
          <>
            <form className="share-search" onSubmit={submitSearch}>
              <input aria-label="Search music" value={query} onChange={event => setQuery(event.target.value)}
                placeholder={`Search ${kind === 'track' ? 'tracks' : 'albums'}…`} />
              <button type="submit" className="primary" disabled={busy || !query.trim()}>
                {busy ? 'Searching…' : 'Search'}
              </button>
            </form>
            <div className="search-tabs share-search-modes" role="group" aria-label="Search source">
              {modes.map(item => (
                <button type="button" key={item.id}
                  className={`tab-button ${mode === item.id ? 'active' : ''}`}
                  aria-pressed={mode === item.id}
                  onClick={() => changeMode(item.id)}>{item.label}</button>
              ))}
            </div>
          </>
        )}

        <div className="share-results-heading">
          <strong>{kind === 'playlist' ? 'Your playlists' : 'Search results'}</strong>
          <span>{candidates.length ? `${candidates.length} available` : ''}</span>
        </div>
        <div className="share-candidates" aria-label="Choose music to share">
          {candidates.map(item => (
            <button type="button" key={`${item.type}:${item.source}:${item.id}`}
              className={selected?.id === item.id && selected?.source === item.source ? 'selected' : ''}
              onClick={() => { setSelected(item); setNewUrl('') }}>
              <Artwork src={item.art} alt="" size="sm" />
              <span className="share-candidate-info">
                <strong title={item.title}>{item.title}</strong>
                <small title={`${item.artist}${item.album ? ` · ${item.album}` : ''}`}>
                  {item.artist}{item.album ? ` · ${item.album}` : ''}
                </small>
              </span>
              <span className="share-candidate-source">{item.source === 'subsonic' ? 'Library' : item.source === 'ytmusic' ? 'YTMusic' : 'Playlist'}</span>
              <span className="share-candidate-action">{selected?.id === item.id && selected?.source === item.source ? '✓' : '+'}</span>
            </button>
          ))}
          {!candidates.length && (
            <p className="muted share-empty">
              {kind === 'playlist' ? 'No playlists found.' : busy ? 'Searching…' : searched ? 'No results found.' : 'Search to find music to share.'}
            </p>
          )}
        </div>

        {selected && (
          <div className="share-create-footer">
            <div className="share-selection">
              <strong title={selected.title}>Selected: {selected.title}</strong>
              <label>Expires
                <select value={days} onChange={event => setDays(Number(event.target.value))}>
                  {config.expiration_days.map(value => (
                    <option value={value} key={value}>{value === 0 ? 'Never' : value === 1 ? '1 day' : `${value} days`}</option>
                  ))}
                </select>
              </label>
            </div>
            <button type="button" className="primary" disabled={busy} onClick={() => void createShare()}>
              {busy ? 'Creating…' : 'Create & copy link'}
            </button>
          </div>
        )}
        {newUrl && (
          <div className="share-created-link">
            <strong>New share link</strong>
            <div className="share-created-link-row">
              <input readOnly value={newUrl} aria-label="New share URL" onFocus={event => event.currentTarget.select()} />
              <button type="button" onClick={() => void copy(newUrl)}>Copy</button>
              <a href={newUrl} target="_blank" rel="noopener noreferrer">Open</a>
            </div>
          </div>
        )}
      </section>

      <section className="share-existing-section">
        <h2>Your share links</h2>
        <div className="share-list">
          {shares.map(share => (
            <article key={share.token} className={share.expired ? 'expired' : ''}>
              <div className="share-list-main">
                <Artwork src={share.art_url || ''} alt="" size="sm" />
                <div className="share-list-copy">
                  <strong title={share.title}>{share.title}</strong>
                  <small>{share.resource_type} · {share.expired ? 'Expired' : share.expires_at ? `Expires ${new Date(share.expires_at).toLocaleDateString()}` : 'Never expires'}</small>
                </div>
              </div>
              <div className="share-list-actions">
                <button type="button" disabled={share.expired} onClick={() => void copy(urlFor(share))}>Copy</button>
                <a href={urlFor(share)} target="_blank" rel="noopener noreferrer">Open</a>
                <button type="button" className="danger" onClick={() => void revoke(share)}>Revoke</button>
              </div>
            </article>
          ))}
          {!shares.length && <p className="muted">No share links yet.</p>}
        </div>
      </section>
    </div>
  )
}
