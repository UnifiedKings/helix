import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { QueueItem } from '../api/types'

type LyricsLine = {
  time_ms: number
  text: string
}

type LyricsResponse = {
  found: boolean
  instrumental: boolean
  plain_lyrics: string
  synced_lyrics: string
  lines: LyricsLine[]
  source: string
  source_id?: number | null
  cached?: boolean
  matched?: {
    track_name?: string
    artist_name?: string
    album_name?: string
    duration_ms?: number
  } | null
}

type LyricsMode = 'synced' | 'plain'

type Props = {
  open: boolean
  track: QueueItem | null | undefined
  onClose: () => void
}

function lyricsQuery(track: QueueItem) {
  const params = new URLSearchParams({
    title: track.title,
    artist: track.artist,
  })
  if (track.album) params.set('album', track.album)
  if (track.duration_ms && track.duration_ms > 0) params.set('duration_ms', String(Math.round(track.duration_ms)))
  return params.toString()
}

function currentPlaybackMs() {
  const input = document.querySelector<HTMLInputElement>('.playback-bar .scrub-input')
  const value = Number(input?.value ?? 0)
  return Number.isFinite(value) ? Math.max(0, value * 1000) : 0
}

function seekTo(timeMs: number) {
  const input = document.querySelector<HTMLInputElement>('.playback-bar .scrub-input')
  if (!input) return
  const max = Number(input.max || 0)
  const targetSeconds = Math.max(0, timeMs / 1000)
  const next = max > 0 ? Math.min(max, targetSeconds) : targetSeconds
  input.value = String(next)
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
}

export function LyricsPanel({ open, track, onClose }: Props) {
  const [payload, setPayload] = useState<LyricsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<LyricsMode>('synced')
  const [positionMs, setPositionMs] = useState(0)
  const activeLineRef = useRef<HTMLButtonElement | null>(null)
  const trackKey = `${track?.subsonic_song_id ?? ''}|${track?.yt_video_id ?? ''}|${track?.title ?? ''}|${track?.artist ?? ''}|${track?.album ?? ''}|${track?.duration_ms ?? 0}`

  useEffect(() => {
    if (!open || !track) return
    let cancelled = false
    setLoading(true)
    setError('')
    setPayload(null)

    fetch(`/api/lyrics?${lyricsQuery(track)}`, { credentials: 'include' })
      .then(async (response) => {
        const text = await response.text()
        if (!response.ok) {
          let detail = text || `${response.status} ${response.statusText}`
          if (text) {
            try {
              const body = JSON.parse(text) as { detail?: string }
              detail = body.detail || detail
            } catch {
              // Keep the raw response body when it is not JSON.
            }
          }
          throw new Error(detail)
        }
        return JSON.parse(text) as LyricsResponse
      })
      .then((result) => {
        if (cancelled) return
        setPayload(result)
        if ((result.lines ?? []).length > 0) setMode('synced')
        else setMode('plain')
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Could not load lyrics')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [open, trackKey])

  useEffect(() => {
    if (!open || mode !== 'synced') return
    setPositionMs(currentPlaybackMs())
    const timer = window.setInterval(() => setPositionMs(currentPlaybackMs()), 100)
    return () => window.clearInterval(timer)
  }, [open, mode, trackKey])

  const lines = payload?.lines ?? []
  const activeIndex = useMemo(() => {
    if (!lines.length) return -1
    let index = -1
    for (let i = 0; i < lines.length; i += 1) {
      if (lines[i].time_ms <= positionMs + 120) index = i
      else break
    }
    return index
  }, [lines, positionMs])

  useEffect(() => {
    if (!open || mode !== 'synced') return
    activeLineRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [open, mode, activeIndex])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  const hasSynced = lines.length > 0
  const hasPlain = Boolean(payload?.plain_lyrics?.trim())

  return createPortal(
    <div className="lyrics-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="lyrics-panel" role="dialog" aria-modal="true" aria-label="Lyrics" onMouseDown={(event) => event.stopPropagation()}>
        <header className="lyrics-header">
          <div className="lyrics-track-heading">
            <div className="eyebrow">Lyrics</div>
            <div className="lyrics-track-title">{track?.title ?? 'Nothing selected'}</div>
            <div className="muted">{track?.artist ?? ''}{track?.album ? ` • ${track.album}` : ''}</div>
          </div>
          <button type="button" className="icon-button lyrics-close" aria-label="Close lyrics" title="Close" onClick={onClose}>×</button>
        </header>

        {(hasSynced || hasPlain) && (
          <div className="lyrics-tabs" role="tablist" aria-label="Lyrics view">
            {hasSynced && (
              <button type="button" role="tab" aria-selected={mode === 'synced'} data-active={mode === 'synced'} onClick={() => setMode('synced')}>
                Synced
              </button>
            )}
            {hasPlain && (
              <button type="button" role="tab" aria-selected={mode === 'plain'} data-active={mode === 'plain'} onClick={() => setMode('plain')}>
                Plain
              </button>
            )}
          </div>
        )}

        <div className="lyrics-content">
          {loading && <div className="lyrics-state muted">Finding lyrics…</div>}
          {!loading && error && <div className="lyrics-state lyrics-error">{error}</div>}
          {!loading && !error && payload?.instrumental && <div className="lyrics-state">Instrumental track</div>}
          {!loading && !error && payload && !payload.found && <div className="lyrics-state muted">No lyrics found for this track.</div>}

          {!loading && !error && payload?.found && !payload.instrumental && mode === 'synced' && hasSynced && (
            <div className="lyrics-synced" aria-live="off">
              <button
                ref={activeIndex === -1 ? activeLineRef : null}
                type="button"
                className="lyrics-line lyrics-start-line"
                data-active={activeIndex === -1}
                data-past={activeIndex >= 0}
                onClick={() => seekTo(0)}
                title="Seek to beginning"
                aria-label="Seek to beginning of song"
              >
                <span aria-hidden="true">♪</span>
              </button>
              {lines.map((line, index) => {
                const active = index === activeIndex
                const past = index < activeIndex
                return (
                  <button
                    key={`${line.time_ms}-${index}`}
                    ref={active ? activeLineRef : null}
                    type="button"
                    className="lyrics-line"
                    data-active={active}
                    data-past={past}
                    onClick={() => seekTo(line.time_ms)}
                    title="Seek to this line"
                  >
                    {line.text || '♪'}
                  </button>
                )
              })}
            </div>
          )}

          {!loading && !error && payload?.found && !payload.instrumental && mode === 'plain' && hasPlain && (
            <pre className="lyrics-plain">{payload.plain_lyrics}</pre>
          )}

          {!loading && !error && payload?.found && !payload.instrumental && !hasSynced && !hasPlain && (
            <div className="lyrics-state muted">Lyrics were matched, but no displayable text was returned.</div>
          )}
        </div>

        {payload?.found && <footer className="lyrics-source muted">Lyrics from LRCLIB</footer>}
      </section>
    </div>,
    document.body,
  )
}
