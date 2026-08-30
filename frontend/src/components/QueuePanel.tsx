import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { api } from '../api/client'
import type { PlayerState, QueueItem } from '../api/types'
import { Artwork } from './Artwork'

type Props = {
  player: PlayerState | null
  refresh: () => Promise<void>
  run: (action: () => Promise<PlayerState>, audioMode?: 'play' | 'pause' | 'none') => Promise<PlayerState>
}

function formatDuration(ms?: number) {
  if (!ms || ms <= 0) return ''
  const totalSeconds = Math.round(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
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

function QueueRow({
  item,
  active,
  playing,
  canDrag,
  dragging,
  onJump,
  onRemove,
  onPointerDown,
  onPointerMove,
}: {
  item: QueueItem
  active: boolean
  playing: boolean
  canDrag: boolean
  dragging: boolean
  onJump: () => void
  onRemove: () => void
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void
  onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => void
}) {
  return (
    <div
      className={`queue-row queue-row-redesign ${active ? 'active' : ''} ${canDrag ? 'queue-row-draggable' : ''} ${dragging ? 'is-dragging' : ''}`}
      data-queue-item-id={item.id}
      title={canDrag ? 'Drag to reorder' : undefined}
      onPointerDown={canDrag ? onPointerDown : undefined}
      onPointerMove={canDrag ? onPointerMove : undefined}
    >
      {active ? (
        <span className={`queue-playing-bars ${playing ? 'is-playing' : 'is-paused'}`} aria-label={playing ? 'Now playing' : 'Current track'}>
          <span />
          <span />
          <span />
        </span>
      ) : canDrag ? (
        <span className="queue-drag-handle" aria-hidden="true">⁝⁝</span>
      ) : (
        <span className="queue-drag-placeholder" aria-hidden="true">⁝⁝</span>
      )}
      <button className="queue-main" onClick={onJump}>
        <Artwork src={item.art_url} alt={item.title} size="sm" />
        <span>
          <strong>{item.title}</strong>
          <span className="muted">{item.artist}</span>
        </span>
      </button>
      <span className="queue-duration">{formatDuration(item.duration_ms)}</span>
      <button className="queue-remove-icon" onClick={onRemove} aria-label={`Remove ${item.title} from queue`}><span className="queue-remove-glyph" aria-hidden="true">×</span></button>
    </div>
  )
}

export function QueuePanel({ player, refresh, run }: Props) {
  const queue = player?.queue ?? []
  const [displayQueue, setDisplayQueue] = useState<QueueItem[]>(queue)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [reorderError, setReorderError] = useState('')
  const dragIdRef = useRef<string | null>(null)
  const pointerCandidateRef = useRef<{ id: string; x: number; y: number } | null>(null)
  const displayQueueRef = useRef<QueueItem[]>(queue)
  const reorderPendingRef = useRef(false)
  const suppressClicksUntilRef = useRef(0)
  const currentIndex = player?.current_index ?? -1
  const currentItemId = queue[currentIndex]?.id ?? null

  useEffect(() => {
    // Do not let realtime player-state updates overwrite the optimistic drag
    // order while a drag or its PATCH request is still in progress.
    if (!dragIdRef.current && !reorderPendingRef.current) {
      displayQueueRef.current = queue
      setDisplayQueue(queue)
    }
  }, [queue])

  const totalMs = displayQueue.reduce((sum, item) => sum + (item.duration_ms ?? 0), 0)
  const totalMinutes = Math.round(totalMs / 60000)
  const activeStation = player?.active_station ?? null
  const isStationPlaying = Boolean(player?.active_station_id || activeStation)
  const stationName = activeStation?.name || (isStationPlaying ? 'Station radio' : '')

  const persistDragOrder = async () => {
    // The pointer handlers update this ref synchronously as the card moves, so
    // pointer-up always persists the exact order currently shown on screen.
    const itemIds = displayQueueRef.current.map((item) => item.id)
    reorderPendingRef.current = true
    setDraggingId(null)
    setReorderError('')
    try {
      const next = await run(() => api.reorderQueue(itemIds), 'none')
      const committedQueue = next.queue ?? []
      displayQueueRef.current = committedQueue
      setDisplayQueue(committedQueue)
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
      void persistDragOrder()
    }

    window.addEventListener('pointerup', finishPointerDrag)
    window.addEventListener('pointercancel', finishPointerDrag)
    return () => {
      window.removeEventListener('pointerup', finishPointerDrag)
      window.removeEventListener('pointercancel', finishPointerDrag)
    }
  })

  return (
    <aside
      className="queue-panel queue-panel-redesign"
      onClickCapture={(event) => {
        if (Date.now() < suppressClicksUntilRef.current) {
          event.preventDefault()
          event.stopPropagation()
        }
      }}
    >
      <div className="queue-header">
        <h2>Up Next</h2>
        <button
          className="ghost queue-clear-placeholder"
          type="button"
          disabled={!queue.length && !isStationPlaying}
          title={isStationPlaying ? 'Clear queue and stop station radio' : 'Clear queue'}
          onClick={() => {
            if (!queue.length && !isStationPlaying) return
            void run(() => api.clearQueue(), 'pause')
          }}
        >
          Clear
        </button>
      </div>
      {isStationPlaying ? (
        <div className="queue-station-banner">
          <span className="queue-station-icon queue-station-record" aria-hidden="true">
            <span />
          </span>
          <div>
            <span className="queue-station-label">Station radio</span>
            <strong>{stationName}</strong>
          </div>
        </div>
      ) : null}
      {reorderError ? <p className="queue-reorder-error" role="alert">Could not save queue order: {reorderError}</p> : null}
      {displayQueue.length === 0 ? <p className="muted">Nothing queued right now.</p> : null}
      <div className={`queue-list-redesign ${draggingId ? 'is-reordering' : ''}`}>
        {displayQueue.map((item, index) => {
          // Keep only the currently playing item fixed. Every other queue item,
          // including tracks before the current song, can be reordered.
          const isCurrentItem = item.id === currentItemId
          const canDrag = !isCurrentItem
          return (
            <QueueRow
              key={item.id}
              item={item}
              active={isCurrentItem}
              playing={isCurrentItem && Boolean(player?.is_playing)}
              canDrag={canDrag}
              dragging={draggingId === item.id}
              onJump={() => run(() => api.jump(index), 'play')}
              onRemove={async () => {
                await api.removeQueueItem(item.id)
                await refresh()
              }}
              onPointerDown={(event) => {
                if (!canDrag || event.button !== 0 || reorderPendingRef.current) return
                pointerCandidateRef.current = { id: item.id, x: event.clientX, y: event.clientY }
                displayQueueRef.current = displayQueue
              }}
              onPointerMove={(event) => {
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
                if (!draggedId || draggedId === item.id || isCurrentItem) return
                event.preventDefault()
                setDisplayQueue((current) => {
                  const next = moveQueueItem(current, draggedId, item.id)
                  displayQueueRef.current = next
                  return next
                })
              }}
            />
          )
        })}
      </div>
      {displayQueue.length ? <div className="queue-summary"><span>{displayQueue.length} songs</span><span>{totalMinutes} min</span></div> : null}
    </aside>
  )
}
