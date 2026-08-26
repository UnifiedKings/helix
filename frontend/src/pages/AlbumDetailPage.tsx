import { useEffect, useMemo, useState } from 'react'
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

function trackDurationSeconds(track: SearchSong) {
  return track.duration_seconds ?? (track.duration_ms ? Math.round(track.duration_ms / 1000) : 0)
}

function durationLabel(track: SearchSong) {
  const seconds = trackDurationSeconds(track)
  if (!seconds) return ''
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, '0')}`
}

function totalDurationLabel(tracks: SearchSong[]) {
  const total = tracks.reduce((sum, track) => sum + trackDurationSeconds(track), 0)
  if (!total) return ''
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours) return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

export function AlbumDetailPage() {
  const { browseId = '' } = useParams()
  const location = useLocation()
  const albumSource = new URLSearchParams(location.search).get('source') || undefined
  const searchReturn = (location.state as { searchReturn?: unknown } | null)?.searchReturn
  const searchReturnState = searchReturn ? { searchReturn } : undefined
  const player = useOutletContext<PlayerContext>()
  const [album, setAlbum] = useState<AlbumDetail | null>(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState<{ message: string; item: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [albumImportQueued, setAlbumImportQueued] = useState(false)
  const [queuedTrackImports, setQueuedTrackImports] = useState<Set<string>>(() => new Set())

  useEffect(() => {
    setAlbumImportQueued(false)
    setQueuedTrackImports(new Set())
  }, [browseId, albumSource])

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError('')
      try {
        const data = await api.album(browseId, albumSource)
        if (!cancelled) setAlbum(data)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load album')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [browseId, albumSource])

  const albumIsInSubsonic = albumSource === 'subsonic'
  const albumSummary = useMemo(() => {
    if (!album) return ''
    const count = album.tracks.length
    const duration = totalDurationLabel(album.tracks)
    return `${count} ${count === 1 ? 'track' : 'tracks'}${duration ? ` · ${duration}` : ''}`
  }, [album])

  async function addAlbumToSubsonic() {
    if (!album || albumImportQueued) return
    setAlbumImportQueued(true)
    try {
      await api.addAlbumToSubsonic(album)
      setStatus({ message: 'Queued album for Subsonic import', item: album.title })
    } catch (err) {
      setAlbumImportQueued(false)
      setError(err instanceof Error ? err.message : 'Could not queue album for Subsonic import')
    }
  }

  async function addTrackToSubsonic(song: SearchSong, key: string) {
    if (queuedTrackImports.has(key)) return
    setQueuedTrackImports((current) => new Set(current).add(key))
    try {
      await api.addSongToSubsonic(song)
      setStatus({ message: 'Queued track for Subsonic import', item: song.title })
    } catch (err) {
      setQueuedTrackImports((current) => {
        const next = new Set(current)
        next.delete(key)
        return next
      })
      setError(err instanceof Error ? err.message : 'Could not queue track for Subsonic import')
    }
  }

  if (loading) return <div className="page-stack"><p className="muted">Loading album…</p></div>

  return (
    <div className="page-stack detail-page album-detail-page">
      {error ? <div className="error-banner">{error}</div> : null}
      {status ? (
        <div className="album-import-notice" role="status" aria-live="polite">
          <span className="album-import-notice-icon" aria-hidden="true">✓</span>
          <div className="album-import-notice-copy">
            <span>{status.message}</span>
            <span className="album-import-notice-separator" aria-hidden="true">•</span>
            <strong>{status.item}</strong>
          </div>
          <button className="album-import-notice-close" type="button" aria-label="Dismiss notification" onClick={() => setStatus(null)}>×</button>
        </div>
      ) : null}
      {album ? (
        <>
          <section className="album-detail-hero">
            <Artwork src={album.art_url || album.thumbnail_url} alt={album.title} size="lg" />
            <div className="album-detail-copy">
              <span className="eyebrow">Album</span>
              <h1>{album.title}</h1>
              <p className="album-detail-meta">{album.artist}{album.year ? ` · ${album.year}` : ''}</p>

              {albumIsInSubsonic ? (
                <div className="album-library-status"><span aria-hidden="true">✓</span> In Subsonic</div>
              ) : null}

              <div className="album-detail-actions">
                <button className="primary" onClick={() => player.run(() => api.playAlbum(album), 'play')}>▶ <span>Play album</span></button>
                <button onClick={() => player.run(() => api.queueAlbum(album))}>＋ <span>Queue album</span></button>
                {!albumIsInSubsonic ? (
                  <button className="album-subsonic-action" disabled={albumImportQueued} onClick={() => void addAlbumToSubsonic()}>S+ <span>{albumImportQueued ? 'Queued' : 'Add to Subsonic'}</span></button>
                ) : null}
                <Link className="album-back-link" to="/search" state={searchReturnState}>Back to search</Link>
              </div>
            </div>
          </section>

          <section className="album-track-section" aria-labelledby="album-tracks-heading">
            <div className="album-track-heading">
              <h2 id="album-tracks-heading">Tracks</h2>
              <span>{albumSummary}</span>
            </div>

            <div className="album-track-columns" aria-hidden="true">
              <span>#</span>
              <span>Title</span>
              <span>Artist</span>
              <span>Duration</span>
              <span />
            </div>

            <div className="album-track-list">
              {album.tracks.map((track, index) => {
                const song = trackToSong(track, album)
                const songIsInSubsonic = Boolean(song.subsonic_song_id || song.source === 'subsonic')
                const importKey = song.yt_video_id || song.video_id || song.subsonic_song_id || `${song.artist}-${song.title}-${index}`
                const trackImportQueued = queuedTrackImports.has(importKey)
                return (
                  <article className="album-track-row" key={`${song.title}-${song.yt_video_id || song.video_id || song.subsonic_song_id}-${index}`}>
                    <span className="album-track-number">{index + 1}</span>
                    <div className="album-track-title"><strong>{song.title}</strong></div>
                    <span className="album-track-artist">{song.artist}</span>
                    <span className="album-track-duration">{durationLabel(song)}</span>
                    <div className="album-track-actions">
                      <button className="album-icon-action" aria-label={`Play ${song.title}`} title="Play" onClick={() => player.run(() => api.playSong(song), 'play')}>▶</button>
                      <button className="album-icon-action" aria-label={`Add ${song.title} to queue`} title="Add to queue" onClick={() => player.run(() => api.queueSong(song))}>＋</button>
                      {!songIsInSubsonic ? (
                        <button className="album-icon-action album-track-subsonic" disabled={trackImportQueued} aria-label={trackImportQueued ? `${song.title} queued for Subsonic import` : `Add ${song.title} to Subsonic`} title={trackImportQueued ? 'Queued for Subsonic' : 'Add to Subsonic'} onClick={() => void addTrackToSubsonic(song, importKey)}>S+</button>
                      ) : null}
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
