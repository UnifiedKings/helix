import { useEffect, useState } from 'react'
import { Link, useLocation, useOutletContext, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { AlbumDetail, SearchSong } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

function trackToSong(track: SearchSong, album: AlbumDetail): SearchSong {
  return {
    ...track,
    album: track.album || album.title,
    artist: track.artist || album.artist,
    art_url: track.art_url || album.art_url || album.thumbnail_url || '',
    thumbnail_url: track.thumbnail_url || album.thumbnail_url || album.art_url || '',
    source: track.source || 'ytmusic',
  }
}

function durationLabel(track: SearchSong) {
  const seconds = track.duration_seconds ?? (track.duration_ms ? Math.round(track.duration_ms / 1000) : 0)
  if (!seconds) return ''
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, '0')}`
}

export function AlbumDetailPage() {
  const { browseId = '' } = useParams()
  const location = useLocation()
  const searchReturn = (location.state as { searchReturn?: unknown } | null)?.searchReturn
  const searchReturnState = searchReturn ? { searchReturn } : undefined
  const player = useOutletContext<PlayerContext>()
  const [album, setAlbum] = useState<AlbumDetail | null>(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError('')
      try {
        const data = await api.album(browseId)
        if (!cancelled) setAlbum(data)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load album')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [browseId])

  async function addAlbumToSubsonic() {
    if (!album) return
    await api.addAlbumToSubsonic(album)
    setStatus(`Queued album for Subsonic import: ${album.title}`)
  }

  if (loading) return <div className="page-stack"><p className="muted">Loading album…</p></div>

  return (
    <div className="page-stack detail-page">
      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}
      {album ? (
        <>
          <section className="detail-hero">
            <Artwork src={album.art_url || album.thumbnail_url} alt={album.title} size="lg" />
            <div className="detail-copy">
              <span className="eyebrow">Album</span>
              <h1>{album.title}</h1>
              <p className="muted">{album.artist}{album.year ? ` • ${album.year}` : ''}</p>
              <div className="detail-actions">
                <button className="primary" onClick={() => player.run(() => api.playAlbum(album), 'play')}>▶ Play album</button>
                <button onClick={() => player.run(() => api.queueAlbum(album))}>Queue album</button>
                <button onClick={() => void addAlbumToSubsonic()}>Add album to Subsonic</button>
                <Link className="button-link" to="/" state={searchReturnState}>Back to search</Link>
              </div>
            </div>
          </section>

          <section className="panel detail-section">
            <h2>Tracks</h2>
            <div className="detail-track-list">
              {album.tracks.map((track, index) => {
                const song = trackToSong(track, album)
                return (
                  <article className="detail-track-row" key={`${song.title}-${song.yt_video_id || song.video_id}-${index}`}>
                    <span className="track-number">{index + 1}</span>
                    <div className="song-title-cell"><strong>{song.title}</strong><span>{song.artist}</span></div>
                    <span className="song-duration">{durationLabel(song)}</span>
                    <div className="search-row-actions">
                      <button className="icon-button compact-action" title="Play" onClick={() => player.run(() => api.playSong(song), 'play')}>▶</button>
                      <button className="icon-button compact-action" title="Queue" onClick={() => player.run(() => api.queueSong(song))}>＋</button>
                      <button className="compact-text-action" onClick={() => void api.addSongToSubsonic(song).then(() => setStatus(`Queued track for Subsonic import: ${song.title}`))}>Add</button>
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
