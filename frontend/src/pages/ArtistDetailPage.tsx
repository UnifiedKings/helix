import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useOutletContext, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ArtistAlbumsResponse, ArtistDetail, ArtistPopularResponse, ArtistSimilarResponse, SearchAlbum, SearchSong } from '../api/types'
import { AlbumLink } from '../components/AlbumLink'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

type ArtistModalKind = 'popular' | 'similar' | null

function albumBrowseId(album: SearchAlbum) {
  return album.yt_browse_id || album.browse_id || album.browseId || ''
}

function songPayload(song: SearchSong, artist: ArtistDetail): SearchSong {
  return { ...song, artist: song.artist || artist.name, source: song.source || 'ytmusic' }
}



function cleanArtistDescription(description?: string) {
  const raw = (description || '').trim()
  if (!raw) return ''
  return raw
    .replace(/\s*From Wikipedia\s*\([^)]*wikipedia[^)]*\).*$/i, '')
    .replace(/\s*From Wikipedia.*$/i, '')
    .trim()
}

function shortArtistDescription(description?: string) {
  const clean = cleanArtistDescription(description)
  if (!clean) return ''
  const sentences = clean.match(/[^.!?]+[.!?]+[\"'”’)]*|[^.!?]+$/g) || [clean]
  const excerpt = sentences.slice(0, 3).join(' ').trim()
  return excerpt.length > 620 ? `${excerpt.slice(0, 617).trimEnd()}…` : excerpt
}

function formatDuration(song: SearchSong) {
  if (song.duration_seconds) return `${Math.floor(song.duration_seconds / 60)}:${String(Math.round(song.duration_seconds % 60)).padStart(2, '0')}`
  if (song.duration_ms) return `${Math.floor(song.duration_ms / 60000)}:${String(Math.round((song.duration_ms % 60000) / 1000)).padStart(2, '0')}`
  return ''
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
  const [similar, setSimilar] = useState<ArtistSimilarResponse | null>(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [queuedSubsonic, setQueuedSubsonic] = useState<Record<string, boolean>>({})
  const [activeModal, setActiveModal] = useState<ArtistModalKind>(null)

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

  useEffect(() => {
    let cancelled = false
    async function load() {
      setError('')
      try {
        const [artistRes, popularRes, albumRes, similarRes] = await Promise.all([
          api.artist(browseId),
          api.artistPopular(browseId),
          api.artistAlbums(browseId),
          api.artistSimilar(browseId).catch(() => ({ similar_artists: [] } as ArtistSimilarResponse)),
        ])
        if (!cancelled) {
          setArtist(artistRes)
          setPopular(popularRes)
          setAlbums(albumRes)
          setSimilar(similarRes)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load artist')
      }
    }
    void load()
    return () => { cancelled = true }
  }, [browseId])

  useEffect(() => {
    if (!activeModal) return undefined
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setActiveModal(null)
    }

    const body = document.body
    const html = document.documentElement
    const previousBodyOverflow = body.style.overflow
    const previousHtmlOverflow = html.style.overflow
    const scrollbarWidth = window.innerWidth - html.clientWidth
    const previousBodyPaddingRight = body.style.paddingRight

    body.style.overflow = 'hidden'
    html.style.overflow = 'hidden'
    if (scrollbarWidth > 0) body.style.paddingRight = `${scrollbarWidth}px`
    window.addEventListener('keydown', onKeyDown)

    return () => {
      window.removeEventListener('keydown', onKeyDown)
      body.style.overflow = previousBodyOverflow
      html.style.overflow = previousHtmlOverflow
      body.style.paddingRight = previousBodyPaddingRight
    }
  }, [activeModal])

  const releases = useMemo(() => {
    const seen = new Set<string>()
    return [...(albums?.albums ?? []), ...(albums?.singles ?? [])].filter((album) => {
      const key = `${album.title}`.trim().toLocaleLowerCase()
      if (!key || seen.has(key)) return false
      seen.add(key)
      return true
    }).slice(0, 12)
  }, [albums])

  const popularTracks = useMemo(
    () => (popular?.tracks ?? []).map((track) => artist ? songPayload(track, artist) : track),
    [popular, artist],
  )
  const similarArtists = similar?.similar_artists ?? []
  const wikipediaUrl = artist?.wikipedia_url || artist?.description_source_url || ''
  const artistDescription = shortArtistDescription(artist?.description)

  if (!artist && !error) return <div className="page-stack"><p className="muted">Loading artist…</p></div>

  return (
    <div className="page-stack detail-page artist-detail-redesign">
      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}
      {artist ? (
        <>
          <section className="artist-profile-hero">
            <Artwork src={artist.art_url || artist.thumbnail_url} alt={artist.name} size="lg" />
            <div className="artist-profile-copy">
              <span className="eyebrow">Artist</span>
              <h1>{artist.name}</h1>
              {artistDescription ? <p className="artist-profile-description">{artistDescription}</p> : null}
              <div className="artist-profile-actions">
                <Link className="button-link" to="/search" state={searchReturnState}>← Back to search</Link>
                {artist.description_source === 'wikipedia' && wikipediaUrl ? (
                  <a className="button-link artist-wikipedia-link" href={wikipediaUrl} target="_blank" rel="noreferrer">Open Wikipedia ↗</a>
                ) : null}
              </div>
              {artist.description_source === 'wikipedia' ? (
                <p className="artist-bio-attribution">Bio from Wikipedia · CC BY-SA</p>
              ) : null}
            </div>
          </section>

          <div className="artist-profile-main-grid">
            <section className="artist-profile-panel artist-popular-panel">
              <div className="artist-section-heading">
                <h2>Popular tracks</h2>
                {popularTracks.length > 5 ? (
                  <button type="button" className="artist-section-button" onClick={() => setActiveModal('popular')}>View all</button>
                ) : null}
              </div>
              <div className="artist-popular-list">
                {popularTracks.slice(0, 5).map((song, index) => {
                  const importKey = `track:${song.yt_video_id || song.video_id || song.subsonic_song_id || `${song.artist}-${song.title}-${index}`}`
                  const queued = Boolean(queuedSubsonic[importKey])
                  return (
                    <article className="artist-popular-row" key={`${song.title}-${song.yt_video_id || song.video_id}-${index}`}>
                      <span className="artist-popular-number">{index + 1}</span>
                      <Artwork src={song.art_url || song.thumbnail_url || artist.art_url || artist.thumbnail_url} alt={song.title} size="sm" />
                      <div className="artist-popular-copy">
                        <strong>{song.title}</strong>
                        {song.album ? <AlbumLink album={song.album} artist={song.artist || artist.name} source={song.source} /> : <span>{artist.name}</span>}
                      </div>
                      <span className="artist-popular-duration">{formatDuration(song)}</span>
                      <div className="artist-popular-actions">
                        <button className="artist-track-action" data-tooltip="Play" title="Play" onClick={() => player.run(() => api.playSong(song), 'play')}>▶</button>
                        <button className="artist-track-action" data-tooltip="Add to queue" title="Add to queue" onClick={() => player.run(() => api.queueSong(song))}>＋</button>
                        <button
                          className={`artist-track-action artist-track-subsonic${queued ? ' is-queued' : ''}`}
                          disabled={queued}
                          aria-label={queued ? `${song.title} queued for Subsonic import` : `Add ${song.title} to Subsonic`}
                          data-tooltip={queued ? 'Queued for Subsonic' : 'Add to Subsonic'}
                          title={queued ? 'Queued for Subsonic' : 'Add to Subsonic'}
                          onClick={() => void queueSongForSubsonic(song, importKey)}
                        >S+</button>
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>

            <section className="artist-profile-panel artist-similar-panel">
              <div className="artist-section-heading">
                <h2>Similar artists</h2>
                {similarArtists.length > 5 ? (
                  <button type="button" className="artist-section-button" onClick={() => setActiveModal('similar')}>View all</button>
                ) : null}
              </div>
              <div className="artist-similar-list">
                {similarArtists.slice(0, 5).map((item) => {
                  const id = item.browse_id || item.artist_id || ''
                  const content = (
                    <>
                      <Artwork src={item.art_url || item.thumbnail_url} alt={item.name} size="sm" />
                      <strong>{item.name}</strong>
                      <span className="artist-similar-arrow" aria-hidden="true">›</span>
                    </>
                  )
                  return id ? (
                    <Link className="artist-similar-row" to={`/artists/${encodeURIComponent(id)}`} key={`${item.name}-${id}`}>
                      {content}
                    </Link>
                  ) : (
                    <div className="artist-similar-row" key={item.name}>{content}</div>
                  )
                })}
                {similarArtists.length === 0 ? <p className="muted artist-empty-state">No similar artists found.</p> : null}
              </div>
            </section>
          </div>

          <section className="artist-profile-panel artist-releases-panel">
            <div className="artist-section-heading">
              <h2>Releases</h2>
            </div>
            <div className="artist-release-strip">
              {releases.map((album) => {
                const bid = albumBrowseId(album)
                const card = (
                  <>
                    <Artwork src={album.art_url || album.thumbnail_url} alt={album.title} size="lg" />
                    <strong>{album.title}</strong>
                    <span>{album.year || ''}</span>
                  </>
                )
                return bid ? (
                  <Link className="artist-release-card" key={`${album.title}-${bid}`} to={`/albums/${encodeURIComponent(bid)}`} state={searchReturnState}>
                    {card}
                  </Link>
                ) : (
                  <div className="artist-release-card" key={album.title}>{card}</div>
                )
              })}
            </div>
          </section>

          {activeModal ? (
            <div className="artist-modal-backdrop" role="presentation" onClick={() => setActiveModal(null)}>
              <section className="artist-modal" role="dialog" aria-modal="true" aria-labelledby={`artist-modal-title-${activeModal}`} onClick={(event) => event.stopPropagation()}>
                <div className="artist-modal-header">
                  <div>
                    <span className="eyebrow">Artist</span>
                    <h2 id={`artist-modal-title-${activeModal}`}>{activeModal === 'popular' ? 'Popular tracks' : 'Similar artists'}</h2>
                  </div>
                  <button type="button" className="artist-modal-close" aria-label="Close" onClick={() => setActiveModal(null)}>×</button>
                </div>

                <div className="artist-modal-body">
                  {activeModal === 'popular' ? (
                    <div className="artist-modal-list artist-modal-track-list">
                      {popularTracks.map((song, index) => {
                        const importKey = `track:${song.yt_video_id || song.video_id || song.subsonic_song_id || `${song.artist}-${song.title}-${index}`}`
                        const queued = Boolean(queuedSubsonic[importKey])
                        return (
                          <article className="artist-popular-row artist-popular-row-modal" key={`${song.title}-${song.yt_video_id || song.video_id}-${index}`}>
                            <span className="artist-popular-number">{index + 1}</span>
                            <Artwork src={song.art_url || song.thumbnail_url || artist.art_url || artist.thumbnail_url} alt={song.title} size="sm" />
                            <div className="artist-popular-copy">
                              <strong>{song.title}</strong>
                              {song.album ? <AlbumLink album={song.album} artist={song.artist || artist.name} source={song.source} /> : <span>{artist.name}</span>}
                            </div>
                            <span className="artist-popular-duration">{formatDuration(song)}</span>
                            <div className="artist-popular-actions">
                              <button className="artist-track-action" data-tooltip="Play" title="Play" onClick={() => player.run(() => api.playSong(song), 'play')}>▶</button>
                              <button className="artist-track-action" data-tooltip="Add to queue" title="Add to queue" onClick={() => player.run(() => api.queueSong(song))}>＋</button>
                              <button
                                className={`artist-track-action artist-track-subsonic${queued ? ' is-queued' : ''}`}
                                disabled={queued}
                                aria-label={queued ? `${song.title} queued for Subsonic import` : `Add ${song.title} to Subsonic`}
                                data-tooltip={queued ? 'Queued for Subsonic' : 'Add to Subsonic'}
                                title={queued ? 'Queued for Subsonic' : 'Add to Subsonic'}
                                onClick={() => void queueSongForSubsonic(song, importKey)}
                              >S+</button>
                            </div>
                          </article>
                        )
                      })}
                    </div>
                  ) : (
                    <div className="artist-modal-list artist-modal-similar-list">
                      {similarArtists.map((item) => {
                        const id = item.browse_id || item.artist_id || ''
                        const content = (
                          <>
                            <Artwork src={item.art_url || item.thumbnail_url} alt={item.name} size="sm" />
                            <div className="artist-similar-copy">
                              <strong>{item.name}</strong>
                            </div>
                            <span className="artist-similar-arrow" aria-hidden="true">›</span>
                          </>
                        )
                        return id ? (
                          <Link className="artist-similar-row artist-similar-row-modal" to={`/artists/${encodeURIComponent(id)}`} key={`${item.name}-${id}`} onClick={() => setActiveModal(null)}>
                            {content}
                          </Link>
                        ) : (
                          <div className="artist-similar-row artist-similar-row-modal" key={item.name}>{content}</div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </section>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
