import { useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { PlaybackHistoryItem, PlaybackHistoryResponse } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

function dateLabel(value: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function durationLabel(item: PlaybackHistoryItem) {
  const seconds = item.played_ms ? Math.round(item.played_ms / 1000) : item.duration_ms ? Math.round(item.duration_ms / 1000) : 0
  if (!seconds) return ''
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, '0')}`
}

export function HistoryPage() {
  const player = useOutletContext<PlayerContext>()
  const [history, setHistory] = useState<PlaybackHistoryResponse | null>(null)
  const [limit, setLimit] = useState(50)
  const [error, setError] = useState('')

  async function load() {
    try {
      const payload = await api.history()
      setHistory(payload)
      setLimit(payload.limit)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load history')
    }
  }

  useEffect(() => { void load() }, [])

  async function updateLimit() {
    try {
      const payload = await api.setHistoryLimit(limit)
      setHistory(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update history limit')
    }
  }

  return (
    <div className="page-stack detail-page history-page">
      <section className="detail-hero compact-detail-hero">
        <div className="detail-copy">
          <span className="eyebrow">Playback</span>
          <h1>History</h1>
          <p className="muted">Replay tracks Helix has completed or skipped.</p>
        </div>
        <div className="history-limit-control">
          <label>Limit<input type="number" min="0" max="500" value={limit} onChange={(event) => setLimit(Number(event.target.value))} /></label>
          <button onClick={() => void updateLimit()}>Save</button>
        </div>
      </section>
      {error ? <div className="error-banner">{error}</div> : null}
      <section className="panel detail-section">
        <h2>Recent playback</h2>
        <div className="detail-track-list">
          {(history?.items ?? []).map((item) => (
            <article className="history-row" key={item.id}>
              <Artwork src={item.art_url} alt={item.title} size="sm" />
              <div className="song-title-cell"><strong>{item.title}</strong><span>{item.artist}{item.album ? ` • ${item.album}` : ''}</span></div>
              <span className="history-meta">{item.event || 'played'}{item.reason ? ` • ${item.reason}` : ''}</span>
              <span className="history-meta">{durationLabel(item)}</span>
              <span className="history-meta">{dateLabel(item.created_at)}</span>
              <div className="search-row-actions">
                <button className="compact-text-action" onClick={() => player.run(() => api.replayHistory(item.id), 'play')}>Replay</button>
                {item.yt_video_id ? <button className="compact-text-action" onClick={() => void api.addSongToSubsonic(item)}>Add</button> : null}
              </div>
            </article>
          ))}
          {history && history.items.length === 0 ? <p className="muted">No history yet.</p> : null}
        </div>
      </section>
    </div>
  )
}
