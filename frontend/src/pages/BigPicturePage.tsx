import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { Link, useLocation, useNavigate, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { QueueItem } from '../api/types'
import type { usePlayer } from '../hooks/usePlayer'
import '../styles/big-picture.css'

type PlayerContext = ReturnType<typeof usePlayer>

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
}

type LyricsMode = 'synced' | 'plain'

function lyricsQuery(track: QueueItem) {
  const params = new URLSearchParams({ title: track.title, artist: track.artist })
  if (track.album) params.set('album', track.album)
  if (track.duration_ms && track.duration_ms > 0) params.set('duration_ms', String(Math.round(track.duration_ms)))
  return params.toString()
}

function normalizeLyricLine(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

function moveQueueItem(queue: QueueItem[], fromId: string, toId: string) {
  const fromIndex = queue.findIndex((item) => item.id === fromId)
  const toIndex = queue.findIndex((item) => item.id === toId)
  if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return queue
  const next = [...queue]
  const [moved] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)
  return next
}

function formatDuration(ms?: number) {
  const totalSeconds = Math.max(0, Math.floor((ms ?? 0) / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

function IconThumbDown({ filled = false }: { filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M7 3h9.2c1.1 0 2 .72 2.3 1.78l1.2 4.3c.12.42.18.85.18 1.28V12c0 1.1-.9 2-2 2h-4.6l.74 3.5c.13.62-.06 1.27-.51 1.72L12.4 20.33 6.9 14.8V5.1C6.9 3.94 5.96 3 4.8 3H4v11h2.9"
        fill={filled ? 'currentColor' : 'none'}
        stroke={filled ? 'none' : 'currentColor'}
        strokeWidth={1.8}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function IconThumbUp({ filled = false }: { filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <g transform="translate(24 0) scale(-1 1)">
        <path
          d="M17 21H7.8c-1.1 0-2-.72-2.3-1.78l-1.2-4.3a4.7 4.7 0 0 1-.18-1.28V12c0-1.1.9-2 2-2h4.6l-.74-3.5c-.13-.62.06-1.27.51-1.72L11.6 3.67l5.5 5.53v9.7c0 1.16.94 2.1 2.1 2.1h.8V10h-2.9"
          fill={filled ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
    </svg>
  )
}

function boostedAverageColor(data: Uint8ClampedArray) {
  let red = 0
  let green = 0
  let blue = 0
  let totalWeight = 0

  for (let i = 0; i < data.length; i += 4) {
    const alpha = data[i + 3] / 255
    if (alpha < 0.4) continue

    const r = data[i]
    const g = data[i + 1]
    const b = data[i + 2]
    const max = Math.max(r, g, b)
    const min = Math.min(r, g, b)
    const brightness = (r + g + b) / 3

    // Near-black/near-white pixels tend to turn an otherwise colorful cover
    // into a gray average, so give them less influence rather than discarding
    // them entirely.
    const saturation = max ? (max - min) / max : 0
    const edgeWeight = brightness < 24 || brightness > 238 ? 0.18 : 1
    const colorWeight = 0.55 + saturation * 1.45
    const weight = alpha * edgeWeight * colorWeight

    red += r * weight
    green += g * weight
    blue += b * weight
    totalWeight += weight
  }

  if (!totalWeight) return null

  let r = red / totalWeight
  let g = green / totalWeight
  let b = blue / totalWeight

  // Lift very dark covers enough that their ambient color remains visible.
  const peak = Math.max(r, g, b)
  if (peak > 0 && peak < 150) {
    const scale = Math.min(1.8, 150 / peak)
    r *= scale
    g *= scale
    b *= scale
  }

  return `${Math.round(Math.min(255, r))} ${Math.round(Math.min(255, g))} ${Math.round(Math.min(255, b))}`
}

export function BigPicturePage() {
  const player = useOutletContext<PlayerContext>()
  const navigate = useNavigate()
  const location = useLocation()
  const current = player.player?.now_playing ?? null
  const station = player.player?.active_station ?? null
  const queue = player.player?.queue ?? []
  const currentIndex = player.player?.current_index ?? -1
  const currentQueueItemId = queue[currentIndex]?.id ?? null
  const trackIdentity = `${current?.subsonic_song_id ?? ''}|${current?.yt_video_id ?? ''}|${current?.id ?? ''}`
  const [displayQueue, setDisplayQueue] = useState<QueueItem[]>(queue)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [reorderError, setReorderError] = useState('')
  const dragIdRef = useRef<string | null>(null)
  const pointerCandidateRef = useRef<{ id: string; x: number; y: number } | null>(null)
  const displayQueueRef = useRef<QueueItem[]>(queue)
  const reorderPendingRef = useRef(false)
  const suppressClicksUntilRef = useRef(0)
  const [glowColor, setGlowColor] = useState('216 145 44')
  const [actionError, setActionError] = useState('')
  const [liked, setLiked] = useState(false)
  const [disliked, setDisliked] = useState(false)
  const [ratingBusy, setRatingBusy] = useState(false)
  const [queueOpen, setQueueOpen] = useState(false)
  const [positionSeconds, setPositionSeconds] = useState(0)
  const [durationSeconds, setDurationSeconds] = useState(Math.max(0, (current?.duration_ms ?? 0) / 1000))
  const [volume, setVolume] = useState(0.85)
  const lastAudibleVolumeRef = useRef(0.85)
  const [lyricsView, setLyricsView] = useState(() => new URLSearchParams(location.search).get('view') === 'lyrics')
  const [lyricsMode, setLyricsMode] = useState<LyricsMode>('synced')
  const [lyricsPayload, setLyricsPayload] = useState<LyricsResponse | null>(null)
  const [lyricsLoading, setLyricsLoading] = useState(false)
  const [lyricsError, setLyricsError] = useState('')
  const activeLyricRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (lyricsView) setLyricsView(false)
      else navigate('/')
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [lyricsView, navigate])

  useEffect(() => {
    const artUrl = current?.art_url?.trim()
    if (!artUrl) {
      setGlowColor('216 145 44')
      return
    }

    let cancelled = false
    const image = new Image()
    image.crossOrigin = 'anonymous'
    image.decoding = 'async'
    image.onload = () => {
      if (cancelled) return
      try {
        const canvas = document.createElement('canvas')
        canvas.width = 32
        canvas.height = 32
        const context = canvas.getContext('2d', { willReadFrequently: true })
        if (!context) return
        context.drawImage(image, 0, 0, canvas.width, canvas.height)
        const color = boostedAverageColor(context.getImageData(0, 0, canvas.width, canvas.height).data)
        if (color && !cancelled) setGlowColor(color)
      } catch {
        // Cross-origin covers without CORS support still display normally; the
        // glow simply keeps the Helix amber fallback for that track.
      }
    }
    image.src = artUrl

    return () => {
      cancelled = true
      image.onload = null
    }
  }, [current?.art_url])

  useEffect(() => {
    if (!lyricsView || !current) return
    let cancelled = false
    setLyricsLoading(true)
    setLyricsError('')
    setLyricsPayload(null)

    fetch(`/api/lyrics?${lyricsQuery(current)}`, { credentials: 'include' })
      .then(async (response) => {
        const raw = await response.text()
        if (!response.ok) {
          let detail = raw || `${response.status} ${response.statusText}`
          try {
            const parsed = JSON.parse(raw) as { detail?: string }
            detail = parsed.detail || detail
          } catch { /* use raw response */ }
          throw new Error(detail)
        }
        return JSON.parse(raw) as LyricsResponse
      })
      .then((payload) => {
        if (cancelled) return
        setLyricsPayload(payload)
        setLyricsMode((payload.lines ?? []).length ? 'synced' : 'plain')
      })
      .catch((err) => {
        if (!cancelled) setLyricsError(err instanceof Error ? err.message : 'Could not load lyrics')
      })
      .finally(() => {
        if (!cancelled) setLyricsLoading(false)
      })

    return () => { cancelled = true }
  }, [lyricsView, current?.id, current?.title, current?.artist, current?.album, current?.duration_ms])

  useEffect(() => {
    let cancelled = false

    async function loadRatingState() {
      if (!current) {
        setLiked(false)
        setDisliked(false)
        return
      }

      try {
        const [likeState, dislikeState] = await Promise.all([
          api.isLiked(current),
          api.isDisliked(current),
        ])
        if (cancelled) return
        setLiked(Boolean(likeState.liked))
        setDisliked(Boolean(dislikeState.disliked))
      } catch {
        if (cancelled) return
        setLiked(false)
        setDisliked(false)
      }
    }

    void loadRatingState()
    return () => {
      cancelled = true
    }
  }, [trackIdentity])

  useEffect(() => {
    const syncPosition = () => {
      const input = document.querySelector<HTMLInputElement>('.playback-bar .scrub-input')
      if (!input) {
        setDurationSeconds(Math.max(0, (current?.duration_ms ?? 0) / 1000))
        return
      }
      const value = Number(input.value || 0)
      const max = Number(input.max || 0)
      if (Number.isFinite(value)) setPositionSeconds(Math.max(0, value))
      if (Number.isFinite(max) && max > 0) setDurationSeconds(max)
    }

    syncPosition()
    const timer = window.setInterval(syncPosition, 100)
    return () => window.clearInterval(timer)
  }, [current?.id, current?.duration_ms])

  function seekToSeconds(nextSeconds: number) {
    const input = document.querySelector<HTMLInputElement>('.playback-bar .scrub-input')
    const max = Number(input?.max || durationSeconds || 0)
    const next = Math.max(0, max > 0 ? Math.min(max, nextSeconds) : nextSeconds)
    setPositionSeconds(next)
    if (!input) return
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    if (nativeSetter) nativeSetter.call(input, String(next))
    else input.value = String(next)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }


  useEffect(() => {
    const syncVolume = () => {
      const input = document.querySelector<HTMLInputElement>('.playback-bar .volume-input')
      const audio = document.querySelector<HTMLAudioElement>('.playback-bar .audio-player audio')
      const next = Number(audio?.volume ?? input?.value ?? window.localStorage.getItem('helix.volume') ?? 0.85)
      if (!Number.isFinite(next)) return
      const clamped = Math.max(0, Math.min(1, next))
      setVolume(clamped)
      if (clamped > 0.01) lastAudibleVolumeRef.current = clamped
    }

    syncVolume()
    const timer = window.setInterval(syncVolume, 250)
    return () => window.clearInterval(timer)
  }, [])

  function setPlaybackVolume(nextVolume: number) {
    const next = Math.max(0, Math.min(1, nextVolume))
    setVolume(next)
    if (next > 0.01) lastAudibleVolumeRef.current = next

    // Update the actual media element immediately so Big Picture is the source
    // of truth while the user is dragging the slider.
    const audio = document.querySelector<HTMLAudioElement>('.playback-bar .audio-player audio')
    if (audio) audio.volume = next
    window.localStorage.setItem('helix.volume', String(next))

    // AudioPlayer's volume control is a React-controlled range input. Assigning
    // input.value directly can update React's internal value tracker without
    // invoking its onChange handler, which makes the controlled value snap back
    // on the next render. Use the native prototype setter before dispatching the
    // input event so React receives a genuine value change and updates its state.
    const input = document.querySelector<HTMLInputElement>('.playback-bar .volume-input')
    if (input) {
      const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      if (nativeSetter) nativeSetter.call(input, String(next))
      else input.value = String(next)
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
    }
  }

  function toggleMute() {
    if (volume > 0.01) {
      lastAudibleVolumeRef.current = volume
      setPlaybackVolume(0)
    } else {
      setPlaybackVolume(Math.max(0.05, lastAudibleVolumeRef.current || 0.85))
    }
  }

  async function toggleLike() {
    if (!current || ratingBusy) return
    setRatingBusy(true)
    try {
      setActionError('')
      const result = await api.toggleLike(current)
      setLiked(result.liked)
      if (result.liked) setDisliked(false)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not update liked state')
    } finally {
      setRatingBusy(false)
    }
  }

  async function toggleDislike() {
    if (!current || ratingBusy) return
    setRatingBusy(true)
    try {
      setActionError('')
      const result = await api.toggleDislike(current)
      setDisliked(result.disliked)
      if (result.disliked) setLiked(false)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not update disliked state')
    } finally {
      setRatingBusy(false)
    }
  }

  async function togglePlayback() {
    if (!current) return
    try {
      setActionError('')
      if (player.player?.is_playing) await player.run(api.pause, 'pause')
      else await player.run(api.resume, 'play')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not update playback')
    }
  }

  async function skip(direction: 'previous' | 'next') {
    try {
      setActionError('')
      const keepPlaying = Boolean(player.player?.is_playing)
      await player.run(direction === 'previous' ? api.previous : api.next, keepPlaying ? 'play' : 'pause')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : `Could not skip ${direction}`)
    }
  }


  useEffect(() => {
    if (!dragIdRef.current && !reorderPendingRef.current) {
      displayQueueRef.current = queue
      setDisplayQueue(queue)
    }
  }, [queue])

  async function persistQueueOrder() {
    const itemIds = displayQueueRef.current.map((item) => item.id)
    reorderPendingRef.current = true
    setDraggingId(null)
    setReorderError('')
    try {
      const next = await player.run(() => api.reorderQueue(itemIds), 'none')
      const committed = next.queue ?? []
      displayQueueRef.current = committed
      setDisplayQueue(committed)
    } catch (err) {
      displayQueueRef.current = queue
      setDisplayQueue(queue)
      setReorderError(err instanceof Error ? err.message : 'Could not reorder queue')
    } finally {
      dragIdRef.current = null
      pointerCandidateRef.current = null
      reorderPendingRef.current = false
    }
  }

  useEffect(() => {
    const finishPointerDrag = () => {
      pointerCandidateRef.current = null
      if (!dragIdRef.current) return
      suppressClicksUntilRef.current = Date.now() + 250
      void persistQueueOrder()
    }

    window.addEventListener('pointerup', finishPointerDrag)
    window.addEventListener('pointercancel', finishPointerDrag)
    return () => {
      window.removeEventListener('pointerup', finishPointerDrag)
      window.removeEventListener('pointercancel', finishPointerDrag)
    }
  })



  const syncedLyrics = useMemo(() => {
    // LRCLIB/LRC files can contain empty timed rows, especially around
    // instrumental breaks or at the very end of a track. Do not turn those
    // provider rows into music-note entries: the only synthetic note belongs
    // at 0:00 so playback after the final lyric keeps the final real lyric
    // active instead of scrolling to an empty end marker.
    const lines = (lyricsPayload?.lines ?? []).filter((line) => line.text.trim().length > 0)
    if (!lines.length) return []
    return [{ time_ms: 0, text: '♪' }, ...lines]
  }, [lyricsPayload])

  const activeLyricIndex = useMemo(() => {
    if (!syncedLyrics.length) return -1
    const positionMs = positionSeconds * 1000
    let active = 0
    for (let index = 1; index < syncedLyrics.length; index += 1) {
      if (syncedLyrics[index].time_ms <= positionMs + 120) active = index
      else break
    }
    return active
  }, [syncedLyrics, positionSeconds])

  const plainLyricsLines = useMemo(() => {
    const plain = lyricsPayload?.plain_lyrics ?? ''
    const source = plain.split(/\r?\n/).map((line) => line.trimEnd())
    if (!(lyricsPayload?.lines ?? []).length) return source.map((text) => ({ text, time_ms: null as number | null }))

    let searchFrom = 0
    return source.map((line) => {
      const normalized = normalizeLyricLine(line)
      if (!normalized) return { text: line, time_ms: null as number | null }
      const timed = lyricsPayload!.lines
      let match = -1
      for (let index = searchFrom; index < timed.length; index += 1) {
        if (normalizeLyricLine(timed[index].text) === normalized) { match = index; break }
      }
      if (match < 0) {
        for (let index = 0; index < searchFrom; index += 1) {
          if (normalizeLyricLine(timed[index].text) === normalized) { match = index; break }
        }
      }
      if (match < 0) return { text: line, time_ms: null as number | null }
      searchFrom = match + 1
      return { text: line, time_ms: timed[match].time_ms as number | null }
    })
  }, [lyricsPayload])

  useEffect(() => {
    if (!lyricsView || lyricsMode !== 'synced' || activeLyricIndex < 0) return
    const isFinalLyric = activeLyricIndex >= syncedLyrics.length - 1
    activeLyricRef.current?.scrollIntoView({ block: isFinalLyric ? 'end' : 'center', behavior: 'smooth' })
  }, [lyricsView, lyricsMode, activeLyricIndex, syncedLyrics.length])

  const pageStyle = { '--big-picture-glow': glowColor } as CSSProperties
  const backdropStyle = current?.art_url ? { backgroundImage: `url(${current.art_url})` } : undefined

  return (
    <section className={`big-picture-page ${lyricsView ? 'lyrics-view' : ''}`} style={pageStyle}>
      {current?.art_url ? <div className="big-picture-backdrop" style={backdropStyle} aria-hidden="true" /> : null}
      <div className="big-picture-shade" aria-hidden="true" />
      <div className="big-picture-ambient" aria-hidden="true" />

      <button type="button" className="big-picture-exit" onClick={() => navigate('/')}>
        <span aria-hidden="true">↙</span>
        Exit Big Picture
      </button>

      {actionError ? <div className="big-picture-error">{actionError}</div> : null}


      {current && lyricsView ? (
        <section className="big-picture-lyrics-stage" aria-label="Lyrics">
          <header className="big-picture-lyrics-header">
            <div className="big-picture-lyrics-track">
              <span>{current.title}</span>
              <small>{current.artist}</small>
            </div>
            <div className="big-picture-lyrics-tabs" role="tablist" aria-label="Lyrics mode">
              {(lyricsPayload?.lines ?? []).length > 0 ? (
                <button type="button" role="tab" aria-selected={lyricsMode === 'synced'} className={lyricsMode === 'synced' ? 'active' : ''} onClick={() => setLyricsMode('synced')}>Synced</button>
              ) : null}
              {lyricsPayload?.plain_lyrics?.trim() ? (
                <button type="button" role="tab" aria-selected={lyricsMode === 'plain'} className={lyricsMode === 'plain' ? 'active' : ''} onClick={() => setLyricsMode('plain')}>Plain</button>
              ) : null}
            </div>
          </header>

          <div className="big-picture-lyrics-body">
            {lyricsLoading ? <div className="big-picture-lyrics-state">Finding lyrics…</div> : null}
            {!lyricsLoading && lyricsError ? <div className="big-picture-lyrics-state error">{lyricsError}</div> : null}
            {!lyricsLoading && !lyricsError && lyricsPayload?.instrumental ? <div className="big-picture-lyrics-state">Instrumental track</div> : null}
            {!lyricsLoading && !lyricsError && lyricsPayload && !lyricsPayload.found ? <div className="big-picture-lyrics-state">No lyrics found for this track.</div> : null}

            {!lyricsLoading && !lyricsError && lyricsPayload?.found && !lyricsPayload.instrumental && lyricsMode === 'synced' && syncedLyrics.length ? (
              <div className="big-picture-lyrics-synced">
                {syncedLyrics.map((line, index) => {
                  const active = index === activeLyricIndex
                  const past = index < activeLyricIndex
                  return (
                    <button
                      type="button"
                      key={`${line.time_ms}-${index}`}
                      ref={active ? activeLyricRef : null}
                      className="big-picture-lyric-line"
                      data-active={active}
                      data-past={past}
                      onClick={() => seekToSeconds(line.time_ms / 1000)}
                      title={line.time_ms === 0 ? 'Go to beginning' : `Seek to ${formatDuration(line.time_ms)}`}
                    >
                      {line.text || '♪'}
                    </button>
                  )
                })}
              </div>
            ) : null}

            {!lyricsLoading && !lyricsError && lyricsPayload?.found && !lyricsPayload.instrumental && lyricsMode === 'plain' && lyricsPayload.plain_lyrics?.trim() ? (
              <div className="big-picture-lyrics-plain">
                {plainLyricsLines.map((line, index) => line.text.trim() ? (
                  line.time_ms !== null ? (
                    <button type="button" key={index} onClick={() => seekToSeconds(line.time_ms! / 1000)} title={`Seek to ${formatDuration(line.time_ms!)}`}>{line.text}</button>
                  ) : <p key={index}>{line.text}</p>
                ) : <div className="big-picture-lyrics-break" key={index} aria-hidden="true" />)}
              </div>
            ) : null}
          </div>

          {lyricsPayload?.found ? <footer className="big-picture-lyrics-source">Lyrics from LRCLIB</footer> : null}
        </section>
      ) : null}

      {current && !lyricsView ? (
        <div className="big-picture-content">
          <div className="big-picture-art-wrap">
            <div className="big-picture-art-glow" aria-hidden="true" />
            {current.art_url ? (
              <img className="big-picture-art" src={current.art_url} alt={`${current.title} album artwork`} />
            ) : (
              <div className="big-picture-art big-picture-art-placeholder" aria-label="No album artwork"><span aria-hidden="true">♪</span></div>
            )}
          </div>

          <div className="big-picture-copy">
            <span className="big-picture-eyebrow"><span className="playing-bars" aria-hidden="true"><i /><i /><i /></span> Now Playing</span>
            <h1>{current.title}</h1>
            <p className="big-picture-artist">{current.artist}</p>
            {current.album ? <p className="big-picture-album">{current.album}</p> : null}
            {station ? <p className="big-picture-station">From station: <strong>{station.name}</strong></p> : null}

            <div className="big-picture-meta" aria-label="Playback status">
              <span className={player.player?.is_playing ? 'is-playing' : ''}>{player.player?.is_playing ? 'Playing' : 'Paused'}</span>
              <span>{queue.length} in queue</span>
              <span>{formatDuration(current.duration_ms)}</span>
            </div>

          </div>
        </div>
      ) : !current ? (
        <div className="big-picture-empty">
          <span aria-hidden="true">♪</span>
          <h1>Nothing Playing</h1>
          <p>Start a song or station, then come back to Big Picture.</p>
          <Link className="big-picture-button primary" to="/">Back Home</Link>
        </div>
      ) : null}

      <aside className={`big-picture-queue-drawer ${queueOpen ? 'open' : ''}`} aria-label="Up next queue">
        <button
          type="button"
          className="big-picture-queue-handle"
          aria-label={queueOpen ? 'Close queue' : 'Open queue'}
          aria-expanded={queueOpen}
          onClick={() => setQueueOpen((open) => !open)}
        >
          <span className="big-picture-queue-handle-icon" aria-hidden="true">{queueOpen ? '›' : '‹'}</span>
          <span className={`big-picture-queue-handle-bars ${player.player?.is_playing ? 'is-playing' : 'is-paused'}`} aria-hidden="true"><i /><i /><i /></span>
        </button>

        <div className="big-picture-queue-panel" aria-hidden={!queueOpen}>
          <header className="big-picture-queue-header">
            <div>
              <span className="eyebrow">Queue</span>
              <h2>Up Next</h2>
            </div>
            <span>{queue.length} {queue.length === 1 ? 'song' : 'songs'}</span>
          </header>

          {reorderError ? <p className="big-picture-queue-reorder-error" role="alert">Could not save queue order: {reorderError}</p> : null}
          <div
            className={`big-picture-queue-list ${draggingId ? 'is-reordering' : ''}`}
            onClickCapture={(event) => {
              if (Date.now() < suppressClicksUntilRef.current) {
                event.preventDefault()
                event.stopPropagation()
              }
            }}
          >
            {displayQueue.length ? displayQueue.map((item) => {
              const isCurrent = item.id === currentQueueItemId
              const canDrag = !isCurrent
              return (
                <div
                  className={`big-picture-queue-item ${isCurrent ? 'current' : ''} ${canDrag ? 'draggable' : ''} ${draggingId === item.id ? 'is-dragging' : ''}`}
                  key={item.id}
                  data-queue-item-id={item.id}
                  title={canDrag ? 'Click to play. Drag anywhere on the card to reorder.' : 'Current track'}
                  onClick={() => {
                    if (Date.now() < suppressClicksUntilRef.current || reorderPendingRef.current) return
                    void player.run(() => api.jump(displayQueue.findIndex((queueItem) => queueItem.id === item.id)), 'play')
                  }}
                  onPointerDown={canDrag ? (event: ReactPointerEvent<HTMLDivElement>) => {
                    if (event.button !== 0 || reorderPendingRef.current) return
                    pointerCandidateRef.current = { id: item.id, x: event.clientX, y: event.clientY }
                    displayQueueRef.current = displayQueue
                  } : undefined}
                  onPointerMove={canDrag ? (event: ReactPointerEvent<HTMLDivElement>) => {
                    if (reorderPendingRef.current) return
                    const candidate = pointerCandidateRef.current
                    if (!dragIdRef.current && candidate) {
                      const dx = event.clientX - candidate.x
                      const dy = event.clientY - candidate.y
                      if (Math.hypot(dx, dy) < 6) return
                      dragIdRef.current = candidate.id
                      setDraggingId(candidate.id)
                      suppressClicksUntilRef.current = Date.now() + 250
                    }

                    const draggedId = dragIdRef.current
                    if (!draggedId || draggedId === item.id || isCurrent) return

                    const currentQueue = displayQueueRef.current
                    const fromIndex = currentQueue.findIndex((queueItem) => queueItem.id === draggedId)
                    const toIndex = currentQueue.findIndex((queueItem) => queueItem.id === item.id)
                    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return

                    // Only swap once the pointer has crossed well into the target
                    // row. Without this threshold, the DOM reflow after a swap can
                    // put the pointer back over the previous row and make the two
                    // cards rapidly trade places.
                    const rect = event.currentTarget.getBoundingClientRect()
                    const relativeY = event.clientY - rect.top
                    const movingDown = toIndex > fromIndex
                    const crossedThreshold = movingDown
                      ? relativeY >= rect.height * 0.62
                      : relativeY <= rect.height * 0.38
                    if (!crossedThreshold) return

                    event.preventDefault()
                    setDisplayQueue((shownQueue) => {
                      const next = moveQueueItem(shownQueue, draggedId, item.id)
                      displayQueueRef.current = next
                      return next
                    })
                  } : undefined}
                >
                  <div className="big-picture-queue-leading" aria-hidden="true">
                    {isCurrent ? (
                      <span className={`big-picture-queue-playing ${player.player?.is_playing ? 'is-playing' : 'is-paused'}`}><i /><i /><i /></span>
                    ) : (
                      <span className="big-picture-queue-drag-handle">⁝⁝</span>
                    )}
                  </div>
                  <div className="big-picture-queue-art">
                    {item.art_url ? <img src={item.art_url} alt="" /> : <span aria-hidden="true">♪</span>}
                  </div>
                  <div className="big-picture-queue-copy">
                    <strong>{item.title}</strong>
                    <span>{item.artist}</span>
                  </div>
                  <div className="big-picture-queue-duration">{formatDuration(item.duration_ms)}</div>
                </div>
              )
            }) : <p className="big-picture-queue-empty">The queue is empty.</p>}
          </div>
        </div>
      </aside>

      {current ? (
        <div className="big-picture-playback-controls">
          <div className="big-picture-progress">
            <input
              type="range"
              min="0"
              max={Math.max(durationSeconds, 0.01)}
              step="0.1"
              value={Math.min(positionSeconds, Math.max(durationSeconds, 0.01))}
              aria-label="Seek through track"
              style={{ '--big-picture-progress': `${durationSeconds > 0 ? Math.max(0, Math.min(100, (positionSeconds / durationSeconds) * 100)) : 0}%` } as CSSProperties}
              onChange={(event) => seekToSeconds(Number(event.currentTarget.value))}
            />
            <div className="big-picture-progress-times">
              <span>{formatDuration(positionSeconds * 1000)}</span>
              <span>{formatDuration(durationSeconds * 1000)}</span>
            </div>
          </div>
          <div className="big-picture-control-row">
            <div className="big-picture-left-controls" aria-label="Track actions">
              <button
                type="button"
                className={`big-picture-rating-button big-picture-rating-dislike ${disliked ? 'active' : ''}`}
                aria-label="Dislike current track"
                aria-pressed={disliked}
                title="Dislike"
                onClick={() => void toggleDislike()}
                disabled={!current || ratingBusy}
              >
                <IconThumbDown filled={disliked} />
              </button>

              <button
                type="button"
                className={`big-picture-lyrics-control ${lyricsView ? 'active' : ''}`}
                aria-label={lyricsView ? 'Return to Now Playing' : 'Show lyrics'}
                aria-pressed={lyricsView}
                title={lyricsView ? 'Now Playing' : 'Lyrics'}
                onClick={() => setLyricsView((shown) => !shown)}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M9.2 17.4V6.6l9-1.8v10.4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  <circle cx="6.8" cy="17.7" r="2.6" fill="currentColor" />
                  <circle cx="15.8" cy="15.8" r="2.6" fill="currentColor" />
                </svg>
              </button>

              <button
                type="button"
                className={`big-picture-rating-button big-picture-rating-like ${liked ? 'active' : ''}`}
                aria-label="Like current track"
                aria-pressed={liked}
                title="Like"
                onClick={() => void toggleLike()}
                disabled={!current || ratingBusy}
              >
                <IconThumbUp filled={liked} />
              </button>
            </div>

            <div className="big-picture-transport" aria-label="Playback controls">
              <button type="button" className="big-picture-skip big-picture-skip-previous" aria-label="Previous track" onClick={() => void skip('previous')}>
                <span className="big-picture-skip-icon" aria-hidden="true"><i /><b /></span>
              </button>
              <button type="button" className="big-picture-play" aria-label={player.player?.is_playing ? 'Pause' : 'Play'} onClick={() => void togglePlayback()}>
                <span aria-hidden="true">{player.player?.is_playing ? 'Ⅱ' : '▶'}</span>
              </button>
              <button type="button" className="big-picture-skip big-picture-skip-next" aria-label="Next track" onClick={() => void skip('next')}>
                <span className="big-picture-skip-icon" aria-hidden="true"><i /><b /></span>
              </button>
            </div>

            <div className="big-picture-volume">
              <button
                type="button"
                className="big-picture-volume-button"
                aria-label={volume <= 0.01 ? 'Unmute' : 'Mute'}
                onClick={toggleMute}
              >
                <svg className="big-picture-volume-icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3.5 9.25h4.1L12.2 5v14l-4.6-4.25H3.5z" fill="currentColor" />
                  {volume <= 0.01 ? (
                    <>
                      <path d="m15.7 9.1 4.2 4.2M19.9 9.1l-4.2 4.2" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                    </>
                  ) : (
                    <>
                      <path d="M15.4 9.15c1.3 1.35 1.3 4.35 0 5.7" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
                      {volume >= 0.45 ? <path d="M18.1 6.8c3.1 3 3.1 7.4 0 10.4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /> : null}
                    </>
                  )}
                </svg>
              </button>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={volume}
                aria-label="Volume"
                style={{ '--big-picture-volume': `${Math.round(volume * 100)}%` } as CSSProperties}
                onChange={(event) => setPlaybackVolume(Number(event.currentTarget.value))}
              />
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
}
