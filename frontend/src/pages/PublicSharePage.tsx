import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { useParams } from 'react-router-dom'
import { Artwork } from '../components/Artwork'
import { PlaybackBarSurface } from '../components/PlaybackBarSurface'
import '../styles/shares.css'
import '../styles/public-share-shell.css'
import '../styles/public-share-drag.css'

type SharedTrack = { id: string; title: string; artist: string; album: string; duration_ms: number; playable: boolean; art_url: string }
type SharedMedia = { type: 'track' | 'album' | 'playlist'; title: string; artist: string; art_url: string; tracks: SharedTrack[] }
type QueuedTrack = SharedTrack & { queueId: number }

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds)) return '0:00'
  const value = Math.max(0, Math.floor(seconds))
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`
}

export function PublicSharePage() {
  const { token = '' } = useParams()
  const audioRef = useRef<HTMLAudioElement>(null)
  const nextId = useRef(0)
  const playIntent = useRef(false)
  const [media, setMedia] = useState<SharedMedia | null>(null)
  const [queue, setQueue] = useState<QueuedTrack[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(0.8)
  const [repeat, setRepeat] = useState(false)
  const [error, setError] = useState('')
  const [dragId, setDragId] = useState<number | null>(null)
  const dragRef = useRef<{ id: number; pointerId: number; startX: number; startY: number; started: boolean; original: QueuedTrack[] } | null>(null)
  const suppressClickRef = useRef(false)
  const [bigPicture, setBigPicture] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    setMedia(null)
    setError('')
    playIntent.current = false
    dragRef.current = null
    setDragId(null)
    fetch(`/api/public/shares/${encodeURIComponent(token)}`, { signal: controller.signal, cache: 'no-store' })
      .then(async response => {
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || 'Share unavailable')
        return response.json() as Promise<SharedMedia>
      })
      .then(payload => {
        setMedia(payload)
        const tracks = payload.tracks.map(track => ({ ...track, queueId: ++nextId.current }))
        setQueue(tracks)
        setActiveId(tracks.find(track => track.playable)?.queueId ?? null)
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Share unavailable') })
    return () => controller.abort()
  }, [token])

  const activeIndex = queue.findIndex(track => track.queueId === activeId)
  const active = activeIndex >= 0 ? queue[activeIndex] : null

  useEffect(() => {
    const element = audioRef.current
    if (!element) return
    element.pause()
    setPlaying(false)
    setElapsed(0)
    setDuration(0)
    if (!active?.playable) {
      element.removeAttribute('src')
      element.load()
      return
    }
    element.src = `/api/public/shares/${encodeURIComponent(token)}/stream/${encodeURIComponent(active.id)}`
    element.load()
    element.volume = volume
    if (playIntent.current) void element.play().catch(() => { playIntent.current = false; setPlaying(false) })
  }, [activeId, active?.id, token])

  function select(id: number) {
    const track = queue.find(item => item.queueId === id)
    if (!track?.playable) return
    playIntent.current = true
    if (activeId === id) void audioRef.current?.play().catch(() => setPlaying(false))
    else setActiveId(id)
  }

  function step(direction: number) {
    if (!queue.length) return
    for (let offset = 1; offset <= queue.length; offset++) {
      const candidate = queue[(Math.max(activeIndex, 0) + direction * offset + queue.length * (offset + 1)) % queue.length]
      if (candidate.playable) { setActiveId(candidate.queueId); return }
    }
  }

  function remove(id: number) {
    const index = queue.findIndex(item => item.queueId === id)
    const remaining = queue.filter(item => item.queueId !== id)
    setQueue(remaining)
    if (id === activeId) {
      setActiveId(remaining.slice(index).concat(remaining.slice(0, index)).find(item => item.playable)?.queueId ?? null)
    }
  }

  function move(from: number, to: number) {
    if (from === to) return
    setQueue(current => {
      const next = [...current]
      const source = next.findIndex(item => item.queueId === from)
      const target = next.findIndex(item => item.queueId === to)
      if (source < 0 || target < 0) return current
      next.splice(target, 0, ...next.splice(source, 1))
      return next
    })
  }

  function startDrag(event: ReactPointerEvent<HTMLDivElement>, id: number) {
    if (event.button !== 0 || dragRef.current) return
    if (event.target instanceof Element && event.target.closest('.helix-share-row-actions')) return
    dragRef.current = { id, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, started: false, original: queue }
  }

  useEffect(() => {
    function onMove(event: PointerEvent) {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return
      if (!drag.started) {
        if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 5) return
        drag.started = true
        suppressClickRef.current = true
        setDragId(drag.id)
      }
      const row = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-shared-queue-id]')
      if (!row) return
      const target = Number(row.dataset.sharedQueueId)
      if (Number.isFinite(target) && target !== drag.id) move(drag.id, target)
    }
    function finish(event: PointerEvent) {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return
      if (event.type === 'pointercancel' && drag.started) setQueue(drag.original)
      if (drag.started) window.setTimeout(() => { suppressClickRef.current = false }, 0)
      dragRef.current = null
      setDragId(null)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
    }
  })

  function shuffle() {
    setQueue(current => {
      const next = [...current]
      for (let i = next.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1))
        ;[next[i], next[j]] = [next[j], next[i]]
      }
      return next
    })
  }

  function restore() {
    if (!media) return
    const tracks = media.tracks.map(track => ({ ...track, queueId: ++nextId.current }))
    setQueue(tracks)
    setActiveId(tracks.find(track => track.playable)?.queueId ?? null)
  }

  function togglePlayback() {
    const element = audioRef.current
    if (!element || !active?.playable) return
    if (element.paused) {
      playIntent.current = true
      void element.play().catch(() => { playIntent.current = false; setPlaying(false) })
    } else {
      playIntent.current = false
      element.pause()
    }
  }

  if (error) return <main className="public-share-shell"><section className="public-share-error"><h1>Share unavailable</h1><p>{error}</p></section></main>
  if (!media) return <main className="public-share-shell"><p className="public-share-loading">Loading shared music…</p></main>

  return (
    <main className={`helix-share-shell ${bigPicture ? 'helix-share-big-picture' : ''}`}>
      <audio ref={audioRef}
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
        onTimeUpdate={event => setElapsed(event.currentTarget.currentTime)}
        onLoadedMetadata={event => setDuration(event.currentTarget.duration || 0)}
        onEnded={() => {
          if (repeat && audioRef.current) {
            audioRef.current.currentTime = 0
            void audioRef.current.play()
          } else if (queue.length > 1) step(1)
          else { playIntent.current = false; setPlaying(false) }
        }} />
      <header className="helix-share-header">
        <div className="helix-share-brand">
          <span className="sidebar-brand-logo" aria-hidden="true" />
          <strong>Helix</strong><span>Shared music</span>
        </div>
        <div className="helix-share-header-actions">
          <button type="button" onClick={() => void navigator.clipboard.writeText(window.location.href)}>Copy link</button>
          <button type="button" onClick={() => setBigPicture(value => !value)}>{bigPicture ? 'Exit Big Picture' : 'Big Picture'}</button>
        </div>
      </header>

      {bigPicture ? (
        <section className="helix-share-big-content" aria-label="Big Picture playback">
          <div className="helix-share-big-art"><Artwork src={active?.art_url || media.art_url || ''} alt="Current track artwork" size="lg" /></div>
          <div className="helix-share-big-info">
            <span className="helix-share-overline">NOW PLAYING</span>
            <h1>{active?.title || 'Queue empty'}</h1>
            <p>{active?.artist || media.artist}</p>
            <span>{media.title} · {queue.length} tracks</span>
            <button type="button" onClick={() => setBigPicture(false)}>View queue</button>
          </div>
        </section>
      ) : (
        <section className="helix-share-center" aria-label="Shared queue">
          <div className="helix-share-queue-heading">
            <div><span className="helix-share-overline">SHARED {media.type.toUpperCase()}</span><h1>Queue</h1><p>{media.title} · {queue.length} {queue.length === 1 ? 'track' : 'tracks'}</p></div>
            <div className="helix-share-queue-actions">
              <button type="button" onClick={shuffle} disabled={queue.length < 2}>Shuffle</button>
              <button type="button" aria-pressed={repeat} onClick={() => setRepeat(value => !value)}>Repeat {repeat ? 'on' : 'off'}</button>
              <button type="button" onClick={restore}>Reset queue</button>
            </div>
          </div>
          <div className="helix-share-queue-list">
            {queue.map(item => (
              <div key={item.queueId} data-shared-queue-id={item.queueId} className={`helix-share-queue-row queue-row queue-row-redesign ${item.queueId === activeId ? 'active' : ''} ${item.queueId === dragId ? 'is-dragging' : ''}`}
                onPointerDown={event => startDrag(event, item.queueId)}
                onClickCapture={event => {
                  if (!suppressClickRef.current) return
                  suppressClickRef.current = false
                  event.preventDefault()
                  event.stopPropagation()
                }}>
                <span className="queue-drag-handle" role="button" tabIndex={0} aria-label={`Drag ${item.title} to reorder`} title="Drag to reorder"
                  onKeyDown={event => {
                    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
                    event.preventDefault()
                    const index = queue.findIndex(track => track.queueId === item.queueId)
                    const neighbor = queue[index + (event.key === 'ArrowUp' ? -1 : 1)]
                    if (neighbor) move(item.queueId, neighbor.queueId)
                  }}>⁝⁝</span>
                <button type="button" className="queue-main" disabled={!item.playable} onClick={() => select(item.queueId)}>
                  <Artwork src={item.art_url} alt="" size="sm" />
                  <span><strong>{item.title}</strong><span className="muted">{item.artist}{!item.playable ? ' · Unavailable' : ''}</span></span>
                </button>
                <span className="queue-duration">{formatTime(item.duration_ms / 1000)}</span>
                <div className="helix-share-row-actions">
                  <button type="button" className="queue-remove-icon" onClick={() => remove(item.queueId)} aria-label={`Remove ${item.title}`}><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>
                </div>
              </div>
            ))}
            {!queue.length && <p className="muted helix-share-empty">Queue is empty. Use Reset queue to restore the shared tracks.</p>}
          </div>
        </section>
      )}

      <PlaybackBarSurface
        className="helix-share-playbar"
        anonymous
        artwork={active?.art_url || media.art_url || ''}
        title={active?.title}
        artist={active?.artist}
        album={active?.album}
        hasTrack={Boolean(active?.playable)}
        playing={playing}
        repeatTrack={repeat}
        onPrevious={() => step(-1)}
        onNext={() => step(1)}
        onTogglePlayback={togglePlayback}
        onToggleRepeat={() => setRepeat(value => !value)}
        audioControls={
          <div className="audio-player">
            <div className="scrub-row">
              <span>{formatTime(elapsed)}</span>
              <input className="scrub-input" type="range" aria-label="Playback position"
                min={0} max={duration || 0} step="0.1"
                disabled={!active?.playable || !duration}
                value={Math.min(elapsed, duration || 0)}
                onChange={event => {
                  const value = Number(event.target.value)
                  if (audioRef.current) audioRef.current.currentTime = value
                  setElapsed(value)
                }} />
              <span>{formatTime(duration)}</span>
            </div>
            <div className="volume-row">
              <span>Volume</span>
              <input className="volume-input" type="range" aria-label="Volume"
                min={0} max={1} step="0.01" value={volume}
                onChange={event => {
                  const value = Number(event.target.value)
                  setVolume(value)
                  if (audioRef.current) audioRef.current.volume = value
                }} />
            </div>
          </div>
        }
      />
    </main>
  )
}
