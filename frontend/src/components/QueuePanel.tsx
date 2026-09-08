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

function blocksQueueDrag(target: EventTarget | null) {
  if (!(target instanceof Element)) return false
  return Boolean(
    target.closest(
      '.queue-remove-icon, .queue-clear-placeholder, a, input, select, textarea, button:not(.queue-main)',
    ),
  )
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
  onRemovePointerDown,
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
  onRemovePointerDown: (event: ReactPointerEvent<HTMLButtonElement>) => void
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

      <button
        className="queue-remove-icon"
        onPointerDown={onRemovePointerDown}
        onClick={onRemove}
        aria-label={`Remove ${item.title} from queue`}
      >
        <span className="queue-remove-glyph" aria-hidden="true">×</span>
      </button>
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
  const queueListRef = useRef<HTMLDivElement | null>(null)
  const lastAutoScrolledItemRef = useRef<string | null>(null)

  const currentIndex = player?.current_index ?? -1
  const currentItemId = queue[currentIndex]?.id ?? null

  useEffect(() => {
    if (!dragIdRef.current && !reorderPendingRef.current) {
      displayQueueRef.current = queue
      setDisplayQueue(queue)
    }
  }, [queue])

  useEffect(() => {
    if (!currentItemId || lastAutoScrolledItemRef.current === currentItemId) return

    const frame = window.requestAnimationFrame(() => {
      const list = queueListRef.current
      if (!list) return

      const currentRow = Array.from(list.children).find(
        (child) => child instanceof HTMLElement && child.dataset.queueItemId === currentItemId,
      ) as HTMLElement | undefined

      if (!currentRow) return

      const listRect = list.getBoundingClientRect()
      const rowRect = currentRow.getBoundingClientRect()
      list.scrollTop += rowRect.top - listRect.top
      lastAutoScrolledItemRef.current = currentItemId
    })

    return () => window.cancelAnimationFrame(frame)
  }, [currentItemId, displayQueue])

  const totalMs = displayQueue.reduce((sum, item) => sum + (item.duration_ms ?? 0), 0)
  const totalMinutes = Math.round(totalMs / 60000)
  const activeStation = player?.active_station ?? null
  const isStationPlaying = Boolean(player?.active_station_id || activeStation)
  const stationName = activeStation?.name || (isStationPlaying ? 'Station radio' : '')

  const cancelPendingDrag = () => {
    pointerCandidateRef.current = null
    dragIdRef.current = null
    setDraggingId(null)
  }

  const persistDragOrder = async () => {
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
      try {
        await refresh()
      } catch {
        // Preserve the original reorder error.
      }
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
        if (Date.now() >= suppressClicksUntilRef.current) return

        // A real drag that began on the track button will naturally produce a
        // click when released. Suppress that click so dragging does not also jump.
        // Explicit queue controls remain usable immediately.
        const target = event.target
        if (
          target instanceof Element
          && target.closest('.queue-remove-icon, .queue-clear-placeholder')
        ) {
          return
        }

        event.preventDefault()
        event.stopPropagation()
      }}
    >
      <div className="queue-header">
        <h2>Queue</h2>
        <button
          className="ghost queue-clear-placeholder"
          type="button"
          disabled={!queue.length && !isStationPlaying}
          title={isStationPlaying ? 'Clear queue and stop station radio' : 'Clear queue'}
          onClick={() => {
            if (!queue.length && !isStationPlaying) return
            cancelPendingDrag()
            setReorderError('')
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

      {reorderError ? (
        <p className="queue-reorder-error" role="alert">
          Could not save queue order: {reorderError}
        </p>
      ) : null}

      {displayQueue.length === 0 ? <p className="muted">Nothing queued right now.</p> : null}

      <div
        ref={queueListRef}
        className={`queue-list-redesign ${draggingId ? 'is-reordering' : ''}`}
      >
        {displayQueue.map((item, index) => {
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
                cancelPendingDrag()
                setReorderError('')

                setDisplayQueue((current) => {
                  const next = current.filter((queueItem) => queueItem.id !== item.id)
                  displayQueueRef.current = next
                  return next
                })

                try {
                  await api.removeQueueItem(item.id)
                  await refresh()
                } catch {
                  await refresh()
                }
              }}
              onRemovePointerDown={(event) => {
                // The remove control is the one part of the row that must never
                // participate in dragging.
                event.stopPropagation()
                cancelPendingDrag()
              }}
              onPointerDown={(event) => {
                if (!canDrag || event.button !== 0 || reorderPendingRef.current) return
                if (blocksQueueDrag(event.target)) return

                pointerCandidateRef.current = {
                  id: item.id,
                  x: event.clientX,
                  y: event.clientY,
                }
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

      {displayQueue.length ? (
        <div className="queue-summary">
          <span>
            {displayQueue.length} songs <span aria-hidden="true">•</span> {totalMinutes} min
          </span>
        </div>
      ) : null}
    </aside>
  )
}
