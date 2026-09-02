import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import '../styles/quality-upgrades.css'

type Quality = { codec: string; bitrate: number; sample_rate: number; bit_depth: number }
type UpgradeJob = {
  id: string
  title: string
  artist: string
  album: string
  art_url?: string
  status: string
  attempts: number
  best_match_score: number
  last_error: string
  last_search_at?: string | null
  next_search_at?: string | null
  upgraded_at?: string | null
  completion_source: string
  original: Quality
  current: Quality
}


async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
  })
  const text = await res.text()
  if (!res.ok) {
    let message = text || `${res.status} ${res.statusText}`
    try { message = JSON.parse(text).detail ?? message } catch { /* noop */ }
    throw new Error(message)
  }
  return text ? JSON.parse(text) as T : undefined as T
}

function qualityLabel(q: Quality) {
  if (!q?.codec) return 'Waiting for library copy'
  const parts = [q.codec.toUpperCase()]
  if (q.bit_depth) parts.push(`${q.bit_depth}-bit`)
  if (q.sample_rate) parts.push(`${(q.sample_rate / 1000).toFixed(q.sample_rate % 1000 ? 1 : 0)} kHz`)
  if (q.bitrate && !['flac', 'alac', 'wav', 'aiff', 'aif'].includes(q.codec.toLowerCase())) parts.push(`${Math.round(q.bitrate / 1000)} kbps`)
  return parts.join(' • ')
}

const FILTERS = [
  ['all', 'All'],
  ['pending', 'Pending'],
  ['searching', 'Searching'],
  ['no_match', 'No match'],
  ['upgraded', 'Upgraded'],
  ['dormant', 'Dormant'],
  ['failed', 'Failed'],
  ['externally_modified', 'Manually modified'],
  ['reverted', 'Reverted'],
]

export function QualityUpgradesPage() {
  const [items, setItems] = useState<UpgradeJob[]>([])
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [appliedQuery, setAppliedQuery] = useState('')
  const [error, setError] = useState('')
  const refreshInFlight = useRef(false)

  const refreshItems = useCallback(async () => {
    if (refreshInFlight.current) return
    refreshInFlight.current = true
    try {
      const params = new URLSearchParams({ status: filter, q: appliedQuery })
      const data = await request<{ items: UpgradeJob[] }>(`/api/quality-upgrades?${params}`)
      setItems(data.items)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load quality upgrades')
    } finally {
      refreshInFlight.current = false
    }
  }, [filter, appliedQuery])


  async function refresh() {
    await refreshItems()
  }

  useEffect(() => {
    void refreshItems()
  }, [refreshItems])


  useEffect(() => {
    let socket: WebSocket | null = null
    let reconnectTimer: number | null = null
    let reconnectAttempt = 0
    let stopped = false

    const connect = () => {
      if (stopped) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/quality-upgrades`)

      socket.onopen = () => {
        reconnectAttempt = 0
      }

      socket.onmessage = event => {
        try {
          const message = JSON.parse(event.data) as { type?: string }
          if (message.type === 'quality-upgrades.changed') {
            void refreshItems()
          }
        } catch {
          // Ignore malformed realtime messages; the socket will keep running.
        }
      }

      socket.onclose = () => {
        if (stopped) return
        const delay = Math.min(10000, 500 * (2 ** reconnectAttempt))
        reconnectAttempt += 1
        reconnectTimer = window.setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      stopped = true
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
      if (socket && socket.readyState < WebSocket.CLOSING) socket.close()
    }
  }, [refreshItems])

  async function deleteJob(job: UpgradeJob) {
    const confirmed = window.confirm(
      `Delete ${job.artist} — ${job.title} from Quality Upgrades?\n\nThis only removes the quality-upgrade database record. It does not remove the track from Helix or your music library.`
    )
    if (!confirmed) return

    try {
      setError('')
      await request(`/api/quality-upgrades/${job.id}`, { method: 'DELETE' })
      setItems(current => current.filter(item => item.id !== job.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete quality upgrade record')
    }
  }

  async function action(job: UpgradeJob, actionName: 'retry' | 'revert' | 'enable') {
    if (actionName === 'revert' && !window.confirm(`Revert ${job.artist} — ${job.title} to a fresh YTMusic copy? Automatic upgrades for this track will be suppressed.`)) return
    try {
      await request(`/api/quality-upgrades/${encodeURIComponent(job.id)}/${actionName}`, { method: 'POST', body: '{}' })
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed')
    }
  }


  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    items.forEach(item => { c[item.status] = (c[item.status] || 0) + 1 })
    return c
  }, [items])

  return <div className="quality-page">
    <header className="quality-header">
      <div>
        <span className="eyebrow">Library quality</span>
        <h1>Quality Upgrades</h1>
        <p>YTMusic remains the reliable library source. Helix uses slskd only to replace eligible tracks with verified higher-quality copies.</p>
      </div>
      <button onClick={() => void refresh()}>Refresh</button>
    </header>

    {error ? <div className="error-banner">{error}</div> : null}


    <section className="quality-controls">
      <div className="quality-filter-row">
        {FILTERS.map(([value, label]) => <button key={value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}{value !== 'all' && counts[value] ? <span>{counts[value]}</span> : null}</button>)}
      </div>
      <div className="quality-search"><input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') setAppliedQuery(query) }} placeholder="Search artist, track, or album" /><button onClick={() => setAppliedQuery(query)}>Search</button></div>
    </section>

    <div className="quality-list">
      {items.length === 0 ? <div className="quality-empty">No tracks match this filter.</div> : items.map(job => <article className="quality-row" key={job.id}>
        <div className="quality-main">
          <div className="quality-art-wrap">
            {job.art_url ? <img className="quality-art" src={job.art_url} alt="" loading="lazy" /> : <div className="quality-art quality-art-placeholder" aria-hidden="true">♪</div>}
          </div>
          <div className="quality-track">
            <strong>{job.title}</strong>
            <span>{job.artist}{job.album ? ` • ${job.album}` : ''}</span>
            <div className="quality-state-inline">
              <span className={`quality-status status-${job.status}`}>{job.status.replaceAll('_', ' ')}</span>
              {job.best_match_score ? <small>{Math.round(job.best_match_score)}% best match</small> : null}
            </div>
          </div>
        </div>

        <div className="quality-change">
          <div className="quality-change-block">
            <span className="quality-change-label">From</span>
            <span>{qualityLabel(job.original)}</span>
          </div>
          <b>→</b>
          <div className="quality-change-block">
            <span className="quality-change-label">To</span>
            <span>{qualityLabel(job.current)}</span>
          </div>
        </div>

        <div className="quality-actions">
          {['no_match', 'dormant', 'failed', 'searching', 'downloading', 'validating', 'tagging', 'replacing'].includes(job.status) ? <button onClick={() => void action(job, 'retry')}>{["searching", "downloading", "validating", "tagging", "replacing"].includes(job.status) ? "Restart" : "Retry"}</button> : null}
          {job.status === 'upgraded' ? <button className="danger-subtle" onClick={() => void action(job, 'revert')}>Revert</button> : null}
          {['reverted', 'externally_modified'].includes(job.status) ? <button onClick={() => void action(job, 'enable')}>Enable upgrades</button> : null}
          <button className="danger-subtle" onClick={() => void deleteJob(job)}>Delete</button>
        </div>

        {job.last_error ? <div className="quality-error">{job.last_error}</div> : null}
      </article>)}
    </div>
  </div>
}
