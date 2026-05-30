import { useState } from 'react'
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

function QueueRow({
  item,
  active,
  dragging,
  dragOver,
  onJump,
  onRemove,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}: {
  item: QueueItem
  active: boolean
  dragging: boolean
  dragOver: boolean
  onJump: () => void
  onRemove: () => void
  onDragStart: () => void
  onDragOver: () => void
  onDrop: () => void
  onDragEnd: () => void
}) {
  return (
    <div
      className={`queue-row queue-row-redesign queue-row-reorderable ${active ? 'active' : ''} ${dragging ? 'dragging' : ''} ${dragOver ? 'drag-over' : ''}`}
      onDragOver={(event) => {
        event.preventDefault()
        onDragOver()
      }}
      onDrop={(event) => {
        event.preventDefault()
        onDrop()
      }}
    >
      <span
        className="queue-drag-placeholder queue-drag-handle"
        aria-label={`Drag ${item.title} to reorder`}
        draggable
        role="button"
        tabIndex={-1}
        title="Drag to reorder"
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = 'move'
          event.dataTransfer.setData('text/plain', item.id)
          onDragStart()
        }}
        onDragEnd={onDragEnd}
      >
        ⁝⁝
      </span>
      <button className="queue-main" onClick={onJump} title={`Play ${item.title}`}>
        <Artwork src={item.art_url} alt={item.title} size="sm" />
        <span>
          <strong>{item.title}</strong>
          <span className="muted">{item.artist}</span>
        </span>
      </button>
      <span className="queue-duration">{formatDuration(item.duration_ms)}</span>
      <button className="queue-remove-icon" onClick={onRemove} aria-label={`Remove ${item.title} from queue`}>×</button>
    </div>
  )
}

function QueueReorderStyles() {
  return (
    <style>{`
      .queue-row-reorderable {
        transition: transform 140ms ease, border-color 140ms ease, background 140ms ease, box-shadow 140ms ease, opacity 140ms ease;
      }
      .queue-row-reorderable:hover,
      .queue-row-reorderable:focus-within {
        transform: translateX(3px);
        border-color: rgba(124, 92, 255, 0.36);
        background: linear-gradient(90deg, rgba(124, 92, 255, 0.13), rgba(53, 217, 149, 0.04));
        box-shadow: inset 3px 0 0 rgba(124, 92, 255, 0.88);
      }
      .queue-row-reorderable.dragging {
        opacity: 0.55;
        transform: scale(0.992);
      }
      .queue-row-reorderable.drag-over {
        border-color: rgba(53, 217, 149, 0.7);
        background: rgba(53, 217, 149, 0.08);
        box-shadow: inset 0 2px 0 rgba(53, 217, 149, 0.82), 0 0 0 1px rgba(53, 217, 149, 0.13);
      }
      .queue-drag-handle {
        cursor: grab;
        user-select: none;
        border-radius: 8px;
        transition: color 120ms ease, background 120ms ease, transform 120ms ease;
      }
      .queue-drag-handle:hover {
        color: var(--text);
        background: rgba(255,255,255,0.08);
      }
      .queue-drag-handle:active {
        cursor: grabbing;
        transform: scale(0.95);
      }
    `}</style>
  )
}

export function QueuePanel({ player, refresh, run }: Props) {
  const queue = player?.queue ?? []
  const totalMs = queue.reduce((sum, item) => sum + (item.duration_ms ?? 0), 0)
  const totalMinutes = Math.round(totalMs / 60000)
  const activeStation = player?.active_station ?? null
  const isStationQueue = Boolean(player?.active_station_id)
  const [draggedItemId, setDraggedItemId] = useState('')
  const [dragOverItemId, setDragOverItemId] = useState('')
  const [reordering, setReordering] = useState(false)

  async function reorderAroundTarget(targetItemId: string) {
    if (!draggedItemId || draggedItemId === targetItemId || reordering) {
      setDraggedItemId('')
      setDragOverItemId('')
      return
    }

    const itemIds = queue.map((item) => item.id)
    const fromIndex = itemIds.indexOf(draggedItemId)
    const toIndex = itemIds.indexOf(targetItemId)
    if (fromIndex < 0 || toIndex < 0) {
      setDraggedItemId('')
      setDragOverItemId('')
      return
    }

    const [moved] = itemIds.splice(fromIndex, 1)
    itemIds.splice(toIndex, 0, moved)
    setDraggedItemId('')
    setDragOverItemId('')
    setReordering(true)
    try {
      await run(() => api.reorderQueue(itemIds), 'none')
    } finally {
      setReordering(false)
    }
  }

  return (
    <aside className="queue-panel queue-panel-redesign">
      <QueueReorderStyles />
      <div className="queue-header">
        <h2>Queue</h2>
        <button className="ghost queue-clear-placeholder" type="button" disabled title="Clear queue placeholder">Clear</button>
      </div>
      {isStationQueue ? (
        <div className="queue-station-context">
          <span className="queue-station-icon" aria-hidden="true">◉</span>
          <span>
            <strong>Playing from station</strong>
            <span>{activeStation?.name ?? 'Station radio'}</span>
          </span>
        </div>
      ) : null}
      {queue.length === 0 ? <p className="muted">The queue is empty.</p> : null}
      <div
        className="queue-list-redesign"
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragOverItemId('')
        }}
      >
        {queue.map((item, index) => (
          <QueueRow
            key={item.id}
            item={item}
            active={index === player?.current_index}
            dragging={draggedItemId === item.id}
            dragOver={Boolean(draggedItemId && dragOverItemId === item.id && draggedItemId !== item.id)}
            onJump={() => run(() => api.jump(index), 'play')}
            onDragStart={() => setDraggedItemId(item.id)}
            onDragOver={() => setDragOverItemId(item.id)}
            onDragEnd={() => {
              setDraggedItemId('')
              setDragOverItemId('')
            }}
            onDrop={() => void reorderAroundTarget(item.id)}
            onRemove={async () => {
              await api.removeQueueItem(item.id)
              await refresh()
            }}
          />
        ))}
      </div>
      {queue.length ? <div className="queue-summary"><span>{queue.length} songs</span><span>{totalMinutes} min</span></div> : null}
    </aside>
  )
}
