import { api } from '../api/client'
import type { PlayerState, QueueItem } from '../api/types'
import { Artwork } from './Artwork'

type Props = {
  player: PlayerState | null
  refresh: () => Promise<void>
  run: (action: () => Promise<PlayerState>, audioMode?: 'play' | 'pause' | 'none') => Promise<PlayerState>
}

function QueueRow({ item, active, onJump, onRemove }: { item: QueueItem; active: boolean; onJump: () => void; onRemove: () => void }) {
  return (
    <div className={`queue-row ${active ? 'active' : ''}`}>
      <button className="queue-main" onClick={onJump}>
        <Artwork src={item.art_url} alt={item.title} size="sm" />
        <span>
          <strong>{item.title}</strong>
          <span className="muted">{item.artist}</span>
        </span>
      </button>
      <button className="ghost danger" onClick={onRemove}>Remove</button>
    </div>
  )
}

export function QueuePanel({ player, refresh, run }: Props) {
  const queue = player?.queue ?? []
  return (
    <aside className="queue-panel">
      <h2>Queue</h2>
      {queue.length === 0 ? <p className="muted">The queue is empty.</p> : null}
      {queue.map((item, index) => (
        <QueueRow
          key={item.id}
          item={item}
          active={index === player?.current_index}
          onJump={() => run(() => api.jump(index), 'play')}
          onRemove={async () => {
            await api.removeQueueItem(item.id)
            await refresh()
          }}
        />
      ))}
    </aside>
  )
}
