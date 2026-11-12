import { FormEvent, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Playlist, SearchAlbum, SearchSong } from '../api/types'
import '../styles/shares.css'

type Share = {
  token: string
  resource_type: string
  resource_id: string
  resource_source?: string
  title: string
  artist?: string
  album?: string
  art_url?: string
  created_at: string
  expires_at?: string | null
  expired: boolean
  url: string
}

type Config = { expiration_days: number[]; default_expiration_days: number }

type Candidate = {
  type: 'track' | 'album' | 'playlist'
  id: string
  source: 'subsonic' | 'ytmusic' | 'helix'
  title: string
  subtitle: string
  artist?: string
  album?: string
  duration_ms?: number
  art?: string
}

async function json<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail || 'Request failed')
  return body as T
}

function songCandidate(song: SearchSong): Candidate | null {
  const subsonicId = song.subsonic_song_id || ''
  const ytVideoId = song.yt_video_id || song.video_id || song.videoId || ''
  const id = subsonicId || ytVideoId
  if (!id) return null
  return {
    type: 'track',
    id,
    source: subsonicId ? 'subsonic' : 'ytmusic',
    title: song.title,
    subtitle: `${song.artist}${song.album ? ` · ${song.album}` : ''}`,
    artist: song.artist,
    album: song.album || '',
    duration_ms: song.duration_ms ?? (song.duration_seconds ? song.duration_seconds * 1000 : 0),
    art: song.art_url || song.thumbnail_url || '',
  }
}

function albumCandidate(album: SearchAlbum): Candidate | null {
  const subsonicId = album.subsonic_album_id || (album.source === 'subsonic' ? album.browse_id : '') || ''
  const ytBrowseId = album.yt_browse_id || album.browse_id || album.browseId || ''
  const id = subsonicId || ytBrowseId
  if (!id) return null
  return {
    type: 'album',
    id,
    source: subsonicId ? 'subsonic' : 'ytmusic',
    title: album.title,
    subtitle: album.artist || '',
    artist: album.artist || '',
    art: album.art_url || album.thumbnail_url || '',
  }
}

export function SharesPage() {
  const [shares, setShares] = useState<Share[]>([])
  const [config, setConfig] = useState<Config>({ expiration_days: [1, 7, 30, 365, 0], default_expiration_days: 30 })
  const [kind, setKind] = useState<'track' | 'album' | 'playlist'>('track')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Candidate[]>([])
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [days, setDays] = useState(30)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = () => json<Share[]>('/api/shares').then(setShares).catch((err) => setError(err.message))

  useEffect(() => {
    load()
    json<Config>('/api/shares/config')
      .then((next) => {
        setConfig(next)
        setDays(next.default_expiration_days)
      })
      .catch(() => {})
    api.playlists().then(setPlaylists).catch(() => {})
  }, [])

  async function search(event: FormEvent) {
    event.preventDefault()
    if (!query.trim()) return
    setBusy(true)
    setError('')
    try {
      // Hybrid search is intentional: shares can point at either an existing
      // Subsonic item or a Helix/YTMusic discovery result.
      const response = await api.search(query.trim(), 'hybrid')
      if (kind === 'track') {
        setResults((response.songs || []).map(songCandidate).filter((item): item is Candidate => Boolean(item)))
      } else {
        setResults((response.albums || []).map(albumCandidate).filter((item): item is Candidate => Boolean(item)))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setBusy(false)
    }
  }

  async function create() {
    if (!candidate) return
    setBusy(true)
    setError('')
    try {
      const share = await json<Share>('/api/shares', {
        method: 'POST',
        body: JSON.stringify({
          resource_type: candidate.type,
          resource_id: candidate.id,
          resource_source: candidate.source,
          expiration_days: days,
          title: candidate.title,
          artist: candidate.artist || '',
          album: candidate.album || '',
          duration_ms: candidate.duration_ms || 0,
          art_url: candidate.art || '',
        }),
      })
      await navigator.clipboard?.writeText(`${window.location.origin}${share.url}`).catch(() => {})
      setCandidate(null)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create share')
    } finally {
      setBusy(false)
    }
  }

  async function remove(token: string) {
    if (!confirm('Revoke this share link? The music itself will not be changed.')) return
    await json(`/api/shares/${encodeURIComponent(token)}`, { method: 'DELETE' })
    load()
  }

  function copy(share: Share) {
    void navigator.clipboard.writeText(`${window.location.origin}${share.url}`)
  }

  const candidates: Candidate[] = kind === 'playlist'
    ? playlists.map((playlist) => ({
        type: 'playlist',
        id: playlist.id,
        source: 'helix',
        title: playlist.name,
        subtitle: `${playlist.track_count ?? 0} tracks`,
        art: playlist.cover_url,
      }))
    : results

  return (
    <div className="page-stack shares-page">
      <header>
        <span className="eyebrow">Read-only public playback</span>
        <h1>Shares</h1>
        <p className="muted">
          Create revocable links for tracks and albums from Helix search, plus live Helix playlists.
          Public listeners can play, seek, and control their own volume without changing your library.
        </p>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="share-create-card">
        <div className="share-tabs">
          {(['track', 'album', 'playlist'] as const).map((type) => (
            <button
              key={type}
              className={kind === type ? 'active' : ''}
              onClick={() => {
                setKind(type)
                setResults([])
                setCandidate(null)
              }}
            >
              {type[0].toUpperCase() + type.slice(1)}
            </button>
          ))}
        </div>

        {kind !== 'playlist' ? (
          <form onSubmit={search} className="share-search">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={`Search Helix ${kind}s…`}
            />
            <button className="primary" disabled={busy}>Search</button>
          </form>
        ) : null}

        <div className="share-candidates">
          {candidates.map((item) => (
            <button
              key={`${item.type}-${item.source}-${item.id}`}
              onClick={() => setCandidate(item)}
              className={candidate?.id === item.id && candidate?.source === item.source ? 'selected' : ''}
            >
              {item.art ? <img src={item.art} alt="" /> : <span className="share-art-placeholder" />}
              <div>
                <strong>{item.title}</strong>
                <span>{item.subtitle}</span>
                {item.type !== 'playlist' ? <small>{item.source === 'subsonic' ? 'Subsonic' : 'Helix discovery'}</small> : null}
              </div>
            </button>
          ))}
        </div>

        {candidate ? (
          <div className="share-create-footer">
            <div>
              <strong>Share {candidate.title}</strong>
              <label>
                Expires
                <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
                  {config.expiration_days.map((value) => (
                    <option key={value} value={value}>
                      {value === 0 ? 'Never' : value === 1 ? '1 day' : `${value} days`}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <button className="primary" onClick={create} disabled={busy}>Create &amp; copy link</button>
          </div>
        ) : null}
      </section>

      <section>
        <h2>Existing shares</h2>
        <div className="share-list">
          {shares.length ? shares.map((share) => (
            <article key={share.token} className={share.expired ? 'expired' : ''}>
              <div className="share-list-main">
                {share.art_url ? (
                  <img className="share-list-art" src={share.art_url} alt="" />
                ) : (
                  <span className="share-list-art share-art-placeholder" aria-hidden="true" />
                )}
                <div className="share-list-copy">
                  <span className="eyebrow">
                    {share.resource_type}{share.resource_source && share.resource_source !== 'helix' ? ` · ${share.resource_source}` : ''}
                  </span>
                  <strong>{share.title}</strong>
                  {share.artist ? <span className="share-list-artist">{share.artist}</span> : null}
                  <small>
                    {share.expired
                      ? 'Expired'
                      : share.expires_at
                        ? `Expires ${new Date(share.expires_at).toLocaleString()}`
                        : 'Never expires'}
                  </small>
                </div>
              </div>
              <div className="share-list-actions">
                <button onClick={() => copy(share)} disabled={share.expired}>Copy link</button>
                <button className="danger" onClick={() => void remove(share.token)}>Revoke</button>
              </div>
            </article>
          )) : <p className="muted">No share links yet.</p>}
        </div>
      </section>
    </div>
  )
}
