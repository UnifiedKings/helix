import { useEffect, useState } from 'react'
import { Link, useLocation, useOutletContext, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ArtistAlbumsResponse, ArtistDetail, ArtistPopularResponse, SearchAlbum, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

function albumBrowseId(album: SearchAlbum) {
  return album.yt_browse_id || album.browse_id || album.browseId || ''
}

function songPayload(song: SearchSong, artist: ArtistDetail): SearchSong {
  return { ...song, artist: song.artist || artist.name, source: song.source || 'ytmusic' }
}

export function ArtistDetailPage() {
  const { browseId = '' } = useParams()
  const location = useLocation()
  const searchReturn = (location.state as { searchReturn?: unknown } | null)?.searchReturn
  const searchReturnState = searchReturn ? { searchReturn } : undefined
  const player = useOutletContext<PlayerContext>()
  const [artist, setArtist] = useState<ArtistDetail | null>(null)
  const [popular, setPopular] = useState<ArtistPopularResponse | null>(null)
  const [albums, setAlbums] = useState<ArtistAlbumsResponse | null>(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [queuedSubsonic, setQueuedSubsonic] = useState<Record<string, boolean>>({})

  async function queueSongForSubsonic(song: SearchSong, key: string) {
    if (queuedSubsonic[key]) return
    setQueuedSubsonic((current) => ({ ...current, [key]: true }))
    try {
      await api.addSongToSubsonic(song)
      setStatus(`Queued track for Subsonic import: ${song.title}`)
    } catch (err) {
      setQueuedSubsonic((current) => ({ ...current, [key]: false }))
      setError(err instanceof Error ? err.message : `Could not queue ${song.title} for Subsonic import`)
    }
  }

  async function queueAlbumForSubsonic(album: SearchAlbum, key: string) {
    if (queuedSubsonic[key]) return
    setQueuedSubsonic((current) => ({ ...current, [key]: true }))
    try {
      await api.addAlbumToSubsonic(album)
      setStatus(`Queued album for Subsonic import: ${album.title}`)
    } catch (err) {
      setQueuedSubsonic((current) => ({ ...current, [key]: false }))
      setError(err instanceof Error ? err.message : `Could not queue ${album.title} for Subsonic import`)
    }
  }

  useEffect(() => {
    let cancelled = false
    async function load() {
      setError('')
      try {
        const [artistRes, popularRes, albumRes] = await Promise.all([
          api.artist(browseId),
          api.artistPopular(browseId),
          api.artistAlbums(browseId),
        ])
        if (!cancelled) {
          setArtist(artistRes)
          setPopular(popularRes)
          setAlbums(albumRes)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load artist')
      }
    }
    void load()
    return () => { cancelled = true }
  }, [browseId])

  if (!artist && !error) return <div className="page-stack"><p className="muted">Loading artist…</p></div>

  return (
    <div className="page-stack detail-page">
      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}
      {artist ? (
        <>
          <section className="detail-hero artist-hero">
            <Artwork src={artist.art_url || artist.thumbnail_url} alt={artist.name} size="lg" />
            <div className="detail-copy">
              <span className="eyebrow">Artist</span>
              <h1>{artist.name}</h1>
              <p className="muted">{artist.subscriber_count || artist.monthly_listeners || artist.views || 'YTMusic artist'}</p>
              {artist.description ? <p>{artist.description}</p> : null}
              <div className="detail-actions">
                <Link className="button-link" to="/search" state={searchReturnState}>Back to search</Link>
              </div>
            </div>
          </section>

          <section className="panel detail-section">
            <h2>Popular tracks</h2>
            <div className="detail-track-list">
              {(popular?.tracks ?? []).map((track, index) => {
                const song = songPayload(track, artist)
                const importKey = `track:${song.yt_video_id || song.video_id || song.subsonic_song_id || `${song.artist}-${song.title}-${index}`}`
                const queued = Boolean(queuedSubsonic[importKey])
                return (
                  <article className="detail-track-row" key={`${song.title}-${song.yt_video_id || song.video_id}-${index}`}>
                    <span className="track-number">{index + 1}</span>
                    <Artwork src={song.art_url || song.thumbnail_url || artist.art_url || artist.thumbnail_url} alt={song.title} size="sm" />
                    <div className="song-title-cell"><strong>{song.title}</strong><span>{song.album || artist.name}</span></div>
                    <div className="search-row-actions">
                      <button className="icon-button compact-action" data-tooltip="Play" title="Play" onClick={() => player.run(() => api.playSong(song), 'play')}>▶</button>
                      <button className="icon-button compact-action" data-tooltip="Add to queue" title="Add to queue" onClick={() => player.run(() => api.queueSong(song))}>＋</button>
                      <button
                        className={`icon-button compact-action subsonic-add-action${queued ? ' is-queued' : ''}`}
                        disabled={queued}
                        aria-label={queued ? `${song.title} queued for Subsonic import` : `Add ${song.title} to Subsonic`}
                        data-tooltip={queued ? 'Queued for Subsonic' : 'Add to Subsonic'}
                        title={queued ? 'Queued for Subsonic' : 'Add to Subsonic'}
                        onClick={() => void queueSongForSubsonic(song, importKey)}
                      >
                        <i className="subsonic-add-glyph-v2" aria-hidden="true">S+</i>
                        {queued ? <b className="subsonic-state-check" aria-hidden="true">✓</b> : null}
                      </button>
                    </div>
                  </article>
                )
              })}
            </div>
          </section>

          <section className="detail-section">
            <h2>Albums</h2>
            <div className="search-album-strip">
              {[...(albums?.albums ?? []), ...(albums?.singles ?? [])].map((album) => {
                const bid = albumBrowseId(album)
                const importKey = `album:${bid || `${album.artist || artist.name}-${album.title}`}`
                const queued = Boolean(queuedSubsonic[importKey])
                return (
                  <article className="search-album-card" key={`${album.title}-${bid}`}>
                    <Artwork src={album.art_url || album.thumbnail_url} alt={album.title} size="lg" />
                    <div className="album-card-body"><strong>{album.title}</strong><span>{album.year || album.artist || artist.name}</span></div>
                    <div className="album-card-actions">
                      <button className="primary" onClick={() => player.run(() => api.playAlbum(album), 'play')}>▶</button>
                      {bid ? <Link className="button-link" to={`/albums/${encodeURIComponent(bid)}`} state={searchReturnState}>Open</Link> : null}
                      <button
                        className={`icon-button compact-action subsonic-add-action${queued ? ' is-queued' : ''}`}
                        disabled={queued}
                        aria-label={queued ? `${album.title} queued for Subsonic import` : `Add ${album.title} to Subsonic`}
                        data-tooltip={queued ? 'Queued for Subsonic' : 'Add to Subsonic'}
                        title={queued ? 'Queued for Subsonic' : 'Add to Subsonic'}
                        onClick={() => void queueAlbumForSubsonic(album, importKey)}
                      >
                        <i className="subsonic-add-glyph-v2" aria-hidden="true">S+</i>
                        {queued ? <b className="subsonic-state-check" aria-hidden="true">✓</b> : null}
                      </button>
                    </div>
                  </article>
                )
              })}
            </div>
          </section>
        </>
      ) : null}
    </div>
  )
}
