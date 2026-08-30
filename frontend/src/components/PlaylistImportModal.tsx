import { ChangeEvent, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { PlaylistImportCandidate, PlaylistImportPreview, PlaylistImportSource } from '../api/types'
import { Artwork } from './Artwork'

const SOURCES: Array<{ id: PlaylistImportSource; label: string }> = [
  { id: 'helix', label: 'Helix' },
  { id: 'ytmusic', label: 'YTMusic' },
  { id: 'spotify', label: 'Spotify' },
  { id: 'pandora', label: 'Pandora' },
]

function sourceHelp(source: PlaylistImportSource) {
  switch (source) {
    case 'helix':
      return {
        title: 'Import from Helix',
        text: 'Export a playlist from Helix as JSON, then upload that file here.',
        acceptsFile: true,
        acceptsUrl: false,
        fileAccept: '.json,application/json',
      }
    case 'ytmusic':
      return {
        title: 'Import from YouTube Music',
        text: 'For a normal playlist, paste its Share link. For Liked Music, open Liked Music in your browser, save the page as HTML, then upload the saved .html file here.',
        acceptsFile: true,
        acceptsUrl: true,
        fileAccept: '.html,.htm,text/html',
      }
    case 'spotify':
      return {
        title: 'Import from Spotify',
        text: 'Use Exportify to export the Spotify playlist or Liked Songs as CSV, then upload the CSV here.',
        acceptsFile: true,
        acceptsUrl: false,
        fileAccept: '.csv,text/csv',
      }
    case 'pandora':
      return {
        title: 'Import from Pandora',
        text: 'Open the playlist or My Thumbs Up in Pandora, choose Share, copy the playlist link, and paste it here.',
        acceptsFile: false,
        acceptsUrl: true,
        fileAccept: '',
      }
  }
}

function formatDuration(ms?: number) {
  if (!ms) return ''
  const seconds = Math.max(0, Math.round(ms / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function downloadJson(filename: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function safeFilename(name: string) {
  const cleaned = (name || 'playlist').replace(/[\\/:*?"<>|]+/g, '').trim().replace(/\s+/g, '_')
  return `${cleaned || 'playlist'}.json`
}

function sanitizeYtMusicSavedPage(html: string) {
  const documentNode = new DOMParser().parseFromString(html, 'text/html')
  const countText = Array.from(documentNode.querySelectorAll('span')).map((node) => node.textContent?.trim() ?? '').find((text) => /^[\d,]+\s+songs$/i.test(text)) ?? ''
  const reportedMatch = countText.match(/^([\d,]+)\s+songs$/i)
  const reportedCount = reportedMatch ? Number(reportedMatch[1].replace(/,/g, '')) : null
  const shelf = documentNode.querySelector('ytmusic-playlist-shelf-renderer') ?? documentNode
  const tracks: Array<{ title: string; artist: string; album: string; duration_ms: number; yt_video_id: string }> = []
  const seen = new Set<string>()

  for (const row of Array.from(shelf.querySelectorAll('ytmusic-responsive-list-item-renderer'))) {
    const links = Array.from(row.querySelectorAll<HTMLAnchorElement>('a[href]'))
    const watch = links.find((anchor) => anchor.href.includes('/watch?') && new URL(anchor.href, window.location.origin).searchParams.get('v'))
    if (!watch) continue
    const parsed = new URL(watch.href, window.location.origin)
    const videoId = parsed.searchParams.get('v') ?? ''
    const title = watch.textContent?.trim() ?? ''
    if (!videoId || !title || seen.has(videoId)) continue
    seen.add(videoId)

    const artistAnchor = links.find((anchor) => anchor.href.includes('/channel/') || /\/browse\/UC/i.test(anchor.href))
    const artist = artistAnchor?.textContent?.trim() ?? ''
    const albumAnchor = links.find((anchor) => anchor.href.includes('/browse/') && anchor !== artistAnchor && !/\/browse\/UC/i.test(anchor.href))
    const album = albumAnchor?.textContent?.trim() ?? ''
    const text = row.textContent ?? ''
    const durationMatches = [...text.matchAll(/\b(?:(\d+):)?(\d{1,2}):(\d{2})\b/g)]
    const durationParts = durationMatches.at(-1)
    const durationMs = durationParts
      ? ((Number(durationParts[1] || 0) * 3600) + (Number(durationParts[2]) * 60) + Number(durationParts[3])) * 1000
      : 0
    tracks.push({ title, artist, album, duration_ms: durationMs, yt_video_id: videoId })
  }

  if (!tracks.length) throw new Error('No YouTube Music tracks were found in that saved page.')
  return JSON.stringify({ format: 'helix-ytmusic-saved-page', version: 1, reported_count: reportedCount, tracks })
}

type Props = {
  open: boolean
  playlistId: string
  playlistName: string
  onClose: () => void
  onImported: () => void | Promise<void>
}

export function PlaylistImportModal({ open, playlistId, playlistName, onClose, onImported }: Props) {
  const [source, setSource] = useState<PlaylistImportSource>('helix')
  const [url, setUrl] = useState('')
  const [filename, setFilename] = useState('')
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState<PlaylistImportPreview | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [selectedCandidates, setSelectedCandidates] = useState<Record<number, PlaylistImportCandidate>>({})
  const [skipExisting, setSkipExisting] = useState(true)
  const [reviewFilter, setReviewFilter] = useState<'all' | 'attention'>('all')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const help = sourceHelp(source)

  useEffect(() => {
    if (!open) return
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) onClose()
    }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [open, busy, onClose])

  useEffect(() => {
    setUrl('')
    setFilename('')
    setContent('')
    setPreview(null)
    setSelected(new Set())
    setSelectedCandidates({})
    setReviewFilter('all')
    setError('')
  }, [source])

  const selectedCount = selected.size
  const importableCount = useMemo(() => preview?.tracks.filter((track) => track.candidate).length ?? 0, [preview])
  const visibleTracks = useMemo(() => {
    if (!preview) return []
    if (reviewFilter === 'attention') return preview.tracks.filter((track) => track.status === 'review' || track.status === 'unmatched')
    return preview.tracks
  }, [preview, reviewFilter])

  if (!open) return null

  async function readFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    setFilename(file.name)
    setError('')
    try {
      const text = await file.text()
      setContent(source === 'ytmusic' ? sanitizeYtMusicSavedPage(text) : text)
      setPreview(null)
    } catch (err) {
      setContent('')
      setError(err instanceof Error ? err.message : 'Could not read that file.')
    }
  }

  async function createPreview() {
    if (!playlistId) return
    if (help.acceptsFile && !help.acceptsUrl && !content) {
      setError('Choose a file to import first.')
      return
    }
    if (help.acceptsUrl && !help.acceptsFile && !url.trim()) {
      setError('Paste a playlist share link first.')
      return
    }
    if (help.acceptsUrl && help.acceptsFile && !url.trim() && !content) {
      setError('Paste a playlist share link or choose a saved HTML file.')
      return
    }

    setBusy(true)
    setError('')
    try {
      const next = await api.previewPlaylistImport(playlistId, {
        source,
        url: url.trim(),
        filename,
        content,
      })
      setPreview(next)
      setReviewFilter('all')
      const initial = new Set<number>()
      const candidates: Record<number, PlaylistImportCandidate> = {}
      for (const track of next.tracks) {
        if (track.candidate) candidates[track.index] = track.candidate
        if (track.status === 'matched' && track.candidate) initial.add(track.index)
      }
      setSelected(initial)
      setSelectedCandidates(candidates)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not preview this import.')
    } finally {
      setBusy(false)
    }
  }

  async function applyImport() {
    if (!preview || !selected.size) return
    const tracks = [...selected]
      .sort((a, b) => a - b)
      .map((index) => selectedCandidates[index])
      .filter((track): track is PlaylistImportCandidate => Boolean(track))
    if (!tracks.length) return

    setBusy(true)
    setError('')
    try {
      await api.applyPlaylistImport(playlistId, tracks, skipExisting)
      await onImported()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not import these tracks.')
    } finally {
      setBusy(false)
    }
  }

  async function exportCurrentPlaylist() {
    setBusy(true)
    setError('')
    try {
      const payload = await api.exportPlaylist(playlistId)
      downloadJson(safeFilename(playlistName), payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not export this playlist.')
    } finally {
      setBusy(false)
    }
  }

  function toggleTrack(index: number) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  function chooseCandidate(index: number, candidate: PlaylistImportCandidate) {
    setSelectedCandidates((current) => ({ ...current, [index]: candidate }))
    setSelected((current) => new Set(current).add(index))
  }

  return (
    <div className="playlist-import-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onClose()
    }}>
      <section className="playlist-import-modal" role="dialog" aria-modal="true" aria-labelledby="playlist-import-title">
        <header className="playlist-import-modal-header">
          <div>
            <p className="playlist-import-eyebrow">Playlist tools</p>
            <h2 id="playlist-import-title">Import playlist</h2>
          </div>
          <button type="button" className="playlist-import-close" onClick={onClose} disabled={busy} aria-label="Close import playlist">×</button>
        </header>

        <div className="playlist-import-source-tabs" role="tablist" aria-label="Import source">
          {SOURCES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={source === item.id ? 'active' : ''}
              onClick={() => setSource(item.id)}
              disabled={busy}
            >
              {item.label}
            </button>
          ))}
        </div>

        {!preview ? (
          <div className="playlist-import-setup">
            <div className="playlist-import-instructions">
              <h3>{help.title}</h3>
              <p>{help.text}</p>
              {source === 'helix' ? (
                <button type="button" onClick={() => void exportCurrentPlaylist()} disabled={busy}>Export this playlist as JSON</button>
              ) : null}
              {source === 'ytmusic' ? <small className="playlist-import-privacy-note">The saved page is parsed in your browser first; Google session data is not sent to Helix.</small> : null}
              {source === 'spotify' ? (
                <a href="https://exportify.app/" target="_blank" rel="noreferrer">Open Exportify ↗</a>
              ) : null}
            </div>

            {help.acceptsUrl ? (
              <label className="playlist-import-field">
                <span>Playlist share URL</span>
                <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={source === 'pandora' ? 'https://www.pandora.com/playlist/…' : 'https://music.youtube.com/playlist?list=…'} />
              </label>
            ) : null}

            {help.acceptsFile ? (
              <label className="playlist-import-file-field">
                <span>{filename || (source === 'spotify' ? 'Choose Exportify CSV' : source === 'ytmusic' ? 'Choose saved HTML' : 'Choose Helix JSON')}</span>
                <input type="file" accept={help.fileAccept} onChange={readFile} />
              </label>
            ) : null}
          </div>
        ) : (
          <div className="playlist-import-review">
            <div className="playlist-import-review-summary">
              <div>
                <p className="playlist-import-eyebrow">Review import</p>
                <h3>{preview.playlist_name || 'Imported playlist'}</h3>
                <p className="muted">
                  {preview.parsed_count} usable tracks found
                  {preview.reported_count && preview.reported_count !== preview.parsed_count ? ` • ${preview.reported_count} reported by source` : ''}
                </p>
              </div>
              <div className="playlist-import-counts">
                <span><strong>{preview.counts.matched}</strong> matched</span>
                <span><strong>{preview.counts.review}</strong> review</span>
                <span><strong>{preview.counts.unmatched}</strong> unmatched</span>
                <span><strong>{preview.counts.duplicate}</strong> already here</span>
              </div>
            </div>

            <div className="playlist-import-review-tools">
              <label className="playlist-import-skip-option">
                <input type="checkbox" checked={skipExisting} onChange={(event) => setSkipExisting(event.target.checked)} />
                <span>
                  <strong>Skip songs already in this playlist</strong>
                  <small>Enabled by default.</small>
                </span>
              </label>

              <div className="playlist-import-filter" role="group" aria-label="Filter import tracks">
                <button type="button" className={reviewFilter === 'all' ? 'active' : ''} onClick={() => setReviewFilter('all')}>All</button>
                <button type="button" className={reviewFilter === 'attention' ? 'active' : ''} onClick={() => setReviewFilter('attention')}>Needs attention <span>{preview.counts.review + preview.counts.unmatched}</span></button>
              </div>
            </div>

            <div className="playlist-import-track-list">
              {visibleTracks.map((track) => {
                const chosen = selectedCandidates[track.index]
                const canSelect = Boolean(chosen) && track.status !== 'duplicate'
                return (
                  <article className={`playlist-import-track ${track.status}`} key={`${track.index}-${track.source_track.source_track_id}-${track.source_track.title}`}>
                    <input
                      type="checkbox"
                      checked={selected.has(track.index)}
                      disabled={!canSelect || busy}
                      onChange={() => toggleTrack(track.index)}
                      aria-label={`Import ${track.source_track.title}`}
                    />
                    <Artwork src={chosen?.art_url || track.source_track.artwork_url} alt={track.source_track.title} size="sm" />
                    <div className="playlist-import-track-copy">
                      <strong>{track.source_track.title}</strong>
                      <span>{track.source_track.artist}{track.source_track.album ? ` • ${track.source_track.album}` : ''}</span>
                      {chosen && track.status === 'review' ? (
                        <small>Matched to: {chosen.title} — {chosen.artist}</small>
                      ) : null}
                      {track.status === 'unmatched' ? <small>No confident match found.</small> : null}
                      {track.status === 'duplicate' ? <small>Already in this playlist.</small> : null}
                    </div>
                    <div className="playlist-import-track-side">
                      <span className={`playlist-import-status ${track.status}`}>
                        {track.status === 'review' ? 'Needs review' : track.status === 'duplicate' ? 'Already here' : track.status}
                      </span>
                      <span className="muted">{formatDuration(track.source_track.duration_ms)}</span>
                    </div>
                    {track.status === 'review' && track.alternatives.length ? (
                      <select
                        value={`${chosen?.yt_video_id || ''}`}
                        onChange={(event) => {
                          const candidate = track.alternatives.find((item) => item.yt_video_id === event.target.value)
                          if (candidate) chooseCandidate(track.index, candidate)
                        }}
                      >
                        {track.alternatives.map((candidate) => (
                          <option key={candidate.yt_video_id || `${candidate.title}-${candidate.artist}`} value={candidate.yt_video_id || ''}>
                            {candidate.title} — {candidate.artist} ({Math.round((candidate.confidence ?? 0) * 100)}%)
                          </option>
                        ))}
                      </select>
                    ) : null}
                  </article>
                )
              })}
            </div>
          </div>
        )}

        {error ? <div className="playlist-import-error">{error}</div> : null}

        <footer className="playlist-import-footer">
          {preview ? <button type="button" onClick={() => setPreview(null)} disabled={busy}>Back</button> : <button type="button" onClick={onClose} disabled={busy}>Cancel</button>}
          <div className="playlist-import-footer-actions">
            {preview ? <span className="muted">{selectedCount} selected</span> : null}
            {!preview ? (
              <button type="button" className="primary" onClick={() => void createPreview()} disabled={busy}>{busy ? 'Matching tracks…' : 'Review import'}</button>
            ) : (
              <button type="button" className="primary" onClick={() => void applyImport()} disabled={busy || !selectedCount || !importableCount}>{busy ? 'Importing…' : `Import ${selectedCount} tracks`}</button>
            )}
          </div>
        </footer>
      </section>
    </div>
  )
}
