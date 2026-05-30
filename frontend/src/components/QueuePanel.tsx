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

function QueueRow({ item, active, onJump, onRemove }: { item: QueueItem; active: boolean; onJump: () => void; onRemove: () => void }) {
  return (
    <div className={`queue-row queue-row-redesign ${active ? 'active' : ''}`}>
      <span className="queue-drag-placeholder" aria-hidden="true">⁝⁝</span>
      <button className="queue-main" onClick={onJump}>
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

export function QueuePanel({ player, refresh, run }: Props) {
  const queue = player?.queue ?? []
  const totalMs = queue.reduce((sum, item) => sum + (item.duration_ms ?? 0), 0)
  const totalMinutes = Math.round(totalMs / 60000)
  const activeStation = player?.active_station ?? null
  const isStationPlaying = Boolean(player?.active_station_id || activeStation)
  const stationName = activeStation?.name || (isStationPlaying ? 'Station radio' : '')
  return (
    <aside className="queue-panel queue-panel-redesign">
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
      {queue.length === 0 ? <p className="muted">Nothing queued right now.</p> : null}
      <div className="queue-list-redesign">
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
      </div>
      {queue.length ? <div className="queue-summary"><span>{queue.length} songs</span><span>{totalMinutes} min</span></div> : null}
    </aside>
  )
}
