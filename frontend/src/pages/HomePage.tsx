import { useEffect, useMemo, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { HomeSummary, HomeAttentionItem, HomeActivityItem } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

const DISMISSED_ATTENTION_KEY = 'helix.home.dismissedAttention'

function loadDismissedAttentionIds() {
  try {
    const parsed = JSON.parse(localStorage.getItem(DISMISSED_ATTENTION_KEY) || '[]')
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

function saveDismissedAttentionIds(ids: string[]) {
  localStorage.setItem(DISMISSED_ATTENTION_KEY, JSON.stringify(ids))
}

function formatDuration(ms?: number) {
  const totalSeconds = Math.max(0, Math.floor((ms ?? 0) / 1000))
  if (!totalSeconds) return '0:00'
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

function relativeTime(value?: string) {
  if (!value) return ''
  const then = new Date(value).getTime()
  if (!Number.isFinite(then)) return ''
  const deltaSeconds = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (deltaSeconds < 60) return 'just now'
  const minutes = Math.floor(deltaSeconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function AttentionCard({ items, totalItems, onDismiss, onDismissAll }: {
  items: HomeAttentionItem[]
  totalItems: number
  onDismiss: (id: string) => void
  onDismissAll: () => void
}) {
  const visible = items.slice(0, 3)
  const hiddenCount = Math.max(0, totalItems - items.length)
  const hasProblems = visible.length > 0
  return (
    <section className={`home-panel home-attention-panel ${hasProblems ? 'needs-attention' : 'healthy'}`}>
      <div className="home-panel-heading">
        <span className="home-panel-icon" aria-hidden="true">{hasProblems ? '⚠' : '✓'}</span>
        <div>
          <h2>{hasProblems ? 'Needs Attention' : 'System Health'}</h2>
          <p className="muted">{hasProblems ? `${items.length} item${items.length === 1 ? '' : 's'} to review${hiddenCount ? ` • ${hiddenCount} cleared` : ''}` : hiddenCount ? `${hiddenCount} cleared item${hiddenCount === 1 ? '' : 's'}` : 'Everything looks good.'}</p>
        </div>
      </div>
      <div className="home-attention-list">
        {hasProblems ? visible.map((item) => (
          <div className="home-attention-item" key={item.id}>
            <span className="home-attention-dot" aria-hidden="true" />
            <div>
              <strong>{item.title}</strong>
              <span className="muted">{item.detail}</span>
            </div>
            <button
              className="home-attention-clear"
              type="button"
              onClick={() => onDismiss(item.id)}
              aria-label={`Clear ${item.title}`}
            >
              Clear
            </button>
          </div>
        )) : (
          <div className="home-attention-item quiet">
            <span className="home-attention-dot" aria-hidden="true" />
            <div>
              <strong>No playback issues detected</strong>
              <span className="muted">Queue, settings, and recent errors look normal.</span>
            </div>
          </div>
        )}
        {hasProblems && items.length > 1 ? (
          <button className="home-attention-clear-all" type="button" onClick={onDismissAll}>
            Clear all shown issues
          </button>
        ) : null}
      </div>
    </section>
  )
}

function ActivityCard({ items }: { items: HomeActivityItem[] }) {
  return (
    <section className="home-panel home-activity-panel">
      <div className="home-panel-heading compact">
        <span className="home-panel-icon" aria-hidden="true">⌁</span>
        <div>
          <h2>Recent Activity</h2>
          <p className="muted">Last few things Helix knows about.</p>
        </div>
      </div>
      <div className="home-activity-list">
        {items.length ? items.slice(0, 4).map((item) => (
          <div className="home-activity-item" key={item.id}>
            {item.art_url ? (
              <Artwork src={item.art_url} alt={item.title} size="sm" />
            ) : (
              <span className="home-activity-icon" aria-hidden="true">{item.icon || '♪'}</span>
            )}
            <div>
              <strong>{item.title}</strong>
              <span className="muted">{item.detail}</span>
            </div>
            <time>{relativeTime(item.created_at)}</time>
          </div>
        )) : (
          <p className="muted home-empty-copy">No recent activity yet.</p>
        )}
      </div>
    </section>
  )
}

export function HomePage() {
  const player = useOutletContext<PlayerContext>()
  const [summary, setSummary] = useState<HomeSummary | null>(null)
  const [dismissedAttentionIds, setDismissedAttentionIds] = useState<string[]>(() => loadDismissedAttentionIds())
  const [error, setError] = useState('')
  const current = player.player?.now_playing ?? null
  const queue = player.player?.queue ?? []
  const station = player.player?.active_station ?? null
  const activeStation = Boolean(player.player?.active_station_id && station)

  useEffect(() => {
    let cancelled = false
    api.homeSummary()
      .then((payload) => { if (!cancelled) setSummary(payload) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load home summary') })
    return () => { cancelled = true }
  }, [])

  const session = useMemo(() => {
    if (!current) {
      return {
        label: 'Current Session',
        title: 'Nothing Playing',
        subtitle: 'Start with Search, a Station, or a Lobby.',
        mode: 'Idle',
        icon: '♪',
      }
    }
    if (activeStation) {
      return {
        label: 'Station Playing',
        title: station?.name || 'Station',
        subtitle: `${current.title} — ${current.artist}`,
        mode: 'Station',
        icon: '◉',
      }
    }
    return {
      label: 'Current Session',
      title: current.title,
      subtitle: current.artist,
      mode: 'Queue Mode',
      icon: '♪',
    }
  }, [activeStation, current, station?.name])

  const allAttention = summary?.attention ?? []
  const attention = allAttention.filter((item) => !dismissedAttentionIds.includes(item.id))
  const activity = summary?.recent_activity ?? []

  function dismissAttention(id: string) {
    setDismissedAttentionIds((currentIds) => {
      const nextIds = Array.from(new Set([...currentIds, id]))
      saveDismissedAttentionIds(nextIds)
      return nextIds
    })
  }

  function dismissAllVisibleAttention() {
    setDismissedAttentionIds((currentIds) => {
      const nextIds = Array.from(new Set([...currentIds, ...attention.map((item) => item.id)]))
      saveDismissedAttentionIds(nextIds)
      return nextIds
    })
  }

  return (
    <div className="home-page">
      {error ? <div className="error-banner">{error}</div> : null}
      <section className={`home-session-card ${current ? 'active' : 'idle'}`}>
        <div className="home-session-art">
          {current ? <Artwork src={current.art_url} alt={current.title} size="lg" /> : <span aria-hidden="true">{session.icon}</span>}
        </div>
        <div className="home-session-copy">
          <span className="eyebrow">{session.label}</span>
          <div className="home-session-title-row">
            <h1>{session.title}</h1>
            <span className="home-mode-pill">{session.mode}</span>
          </div>
          <p className="muted">{session.subtitle}</p>
          <div className="home-session-meta">
            <span>{player.player?.is_playing ? 'Playing' : current ? 'Paused' : 'Ready'}</span>
            <span>{queue.length} in queue</span>
            {current ? <span>{formatDuration(current.duration_ms)}</span> : null}
            {current?.source ? <span>{current.source === 'subsonic' ? 'Local Library' : current.source}</span> : null}
          </div>
        </div>
        <div className="home-session-actions">
          {activeStation && player.player?.active_station_id ? <Link className="button-link" to="/stations">View Station</Link> : null}
          {current ? <Link className="button-link" to="/search">Find More</Link> : <Link className="button-link primary" to="/search">Search Music</Link>}
        </div>
      </section>

      <section className="home-launch-grid" aria-label="Start something">
        <Link className="home-launch-card" to="/search">
          <span aria-hidden="true">⌕</span>
          <strong>Search Music</strong>
          <small>Find songs, albums, and artists.</small>
        </Link>
        <Link className="home-launch-card" to="/stations">
          <span aria-hidden="true">◉</span>
          <strong>Start Station</strong>
          <small>Create a station and let Helix build the vibe.</small>
        </Link>
        <Link className="home-launch-card" to="/playlists">
          <span aria-hidden="true">♫</span>
          <strong>Open Playlists</strong>
          <small>Play or manage saved playlists.</small>
        </Link>
        <Link className="home-launch-card" to="/lobbies">
          <span aria-hidden="true">◎</span>
          <strong>Open Lobbies</strong>
          <small>Listen together with friends.</small>
        </Link>
      </section>

      <section className="home-lower-grid">
        <AttentionCard items={attention} totalItems={allAttention.length} onDismiss={dismissAttention} onDismissAll={dismissAllVisibleAttention} />
        <ActivityCard items={activity} />
      </section>
    </div>
  )
}
