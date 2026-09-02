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
  reverted_at?: string | null
  completion_source: string
  original: Quality
  current: Quality
}

type FilterDef = {
  value: string
  label: string
  alwaysShow?: boolean
}

const FILTERS: FilterDef[] = [
  { value: 'all', label: 'All', alwaysShow: true },
  { value: 'pending', label: 'Pending', alwaysShow: true },
  { value: 'searching', label: 'Searching', alwaysShow: true },
  { value: 'waiting_search', label: 'Search queue', alwaysShow: true },
  { value: 'waiting_peer', label: 'Peer queue', alwaysShow: true },
  { value: 'no_match', label: 'No match', alwaysShow: true },
  { value: 'upgraded', label: 'Upgraded', alwaysShow: true },
  { value: 'failed', label: 'Failed', alwaysShow: true },
  { value: 'dormant', label: 'Dormant' },
  { value: 'externally_modified', label: 'Manually modified', alwaysShow: true },
  { value: 'reverted', label: 'Reverted', alwaysShow: true },
]

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

function qualityCodec(q: Quality) {
  return q?.codec ? q.codec.toUpperCase() : '—'
}

function qualityDetails(q: Quality) {
  if (!q?.codec) return ''
  const parts: string[] = []
  if (q.bit_depth) parts.push(`${q.bit_depth}-bit`)
  if (q.sample_rate) parts.push(`${(q.sample_rate / 1000).toFixed(q.sample_rate % 1000 ? 1 : 0)} kHz`)
  if (q.bitrate && !['flac', 'alac', 'wav', 'aiff', 'aif'].includes(q.codec.toLowerCase())) {
    parts.push(`${Math.round(q.bitrate / 1000)} kbps`)
  }
  return parts.join(' • ')
}

function hasActualUpgrade(job: UpgradeJob) {
  if (job.status === 'upgraded') return true
  const originalCodec = (job.original?.codec || '').toLowerCase()
  const currentCodec = (job.current?.codec || '').toLowerCase()
  if (!originalCodec || !currentCodec) return false
  return (
    originalCodec !== currentCodec
    || Number(job.original?.bit_depth || 0) !== Number(job.current?.bit_depth || 0)
    || Number(job.original?.sample_rate || 0) !== Number(job.current?.sample_rate || 0)
    || Number(job.original?.bitrate || 0) !== Number(job.current?.bitrate || 0)
  )
}

function prettyStatus(status: string) {
  return status.replaceAll('_', ' ')
}

function parseBackendDate(value: string) {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  return new Date(hasZone ? value : `${value}Z`)
}

function formatRelativeTime(value?: string | null) {
  if (!value) return ''
  const then = parseBackendDate(value)
  if (Number.isNaN(then.getTime())) return ''

  const diffMs = Date.now() - then.getTime()
  const future = diffMs < 0
  const seconds = Math.max(1, Math.round(Math.abs(diffMs) / 1000))

  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
    ['second', 1],
  ]
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  for (const [unit, amount] of units) {
    if (seconds >= amount || unit === 'second') {
      const delta = Math.round(seconds / amount)
      return rtf.format(future ? delta : -delta, unit)
    }
  }
  return ''
}

function statusMeta(job: UpgradeJob) {
  switch (job.status) {
    case 'upgraded':
      return {
        title: 'Upgraded',
        detail: job.upgraded_at ? `Completed ${formatRelativeTime(job.upgraded_at)}` : 'Completed',
      }
    case 'failed':
      return {
        title: 'Failed',
        detail: job.last_error || 'Upgrade failed',
      }
    case 'no_match':
      return {
        title: 'No match',
        detail: job.last_error || 'No acceptable higher-quality copy found',
      }
    case 'pending':
      return {
        title: 'Pending',
        detail: job.next_search_at ? `Queued • ${formatRelativeTime(job.next_search_at)}` : 'Queued',
      }
    case 'searching':
      return {
        title: 'Searching',
        detail: job.last_search_at ? `Active ${formatRelativeTime(job.last_search_at)}` : 'Searching Soulseek',
      }
    case 'waiting_search':
      return {
        title: 'Waiting for search slot',
        detail: 'Waiting briefly so search requests are not sent at the same time',
      }
    case 'waiting_peer':
      return {
        title: 'Waiting for peer',
        detail: 'Soulseek peer has not opened an upload slot yet',
      }
    case 'downloading':
      return {
        title: 'Downloading',
        detail: 'Transfer is actively downloading',
      }
    case 'validating':
      return {
        title: 'Validating',
        detail: 'Verifying downloaded file',
      }
    case 'tagging':
      return {
        title: 'Tagging',
        detail: 'Writing metadata',
      }
    case 'replacing':
      return {
        title: 'Replacing',
        detail: 'Moving upgraded file into library',
      }
    case 'reverted':
      return {
        title: 'Reverted',
        detail: job.reverted_at ? `Suppressed ${formatRelativeTime(job.reverted_at)}` : 'Automatic upgrades disabled',
      }
    case 'externally_modified':
      return {
        title: 'Manually modified',
        detail: 'The library copy changed outside Helix',
      }
    case 'dormant':
      return {
        title: 'Dormant',
        detail: 'Waiting for another retry window',
      }
    default:
      return {
        title: prettyStatus(job.status),
        detail: '',
      }
  }
}

type JobAction = 'retry' | 'revert' | 'enable' | 'delete'

function actionsFor(job: UpgradeJob): JobAction[] {
  if (job.status === 'upgraded') return ['revert', 'delete']
  if (['reverted', 'externally_modified'].includes(job.status)) return ['enable', 'delete']
  if (['pending', 'searching', 'waiting_search', 'waiting_peer', 'downloading', 'validating', 'tagging', 'replacing', 'no_match', 'dormant', 'failed'].includes(job.status)) return ['retry', 'delete']
  return ['delete']
}

function statusDotClass(status: string) {
  if (status === 'upgraded') return 'is-success'
  if (status === 'failed' || status === 'no_match') return 'is-danger'
  if (status === 'searching' || status === 'downloading' || status === 'validating' || status === 'tagging' || status === 'replacing') return 'is-info'
  if (status === 'pending' || status === 'waiting_search' || status === 'waiting_peer' || status === 'dormant' || status === 'reverted') return 'is-warn'
  return 'is-muted'
}

export function QualityUpgradesPage() {
  const [allItems, setAllItems] = useState<UpgradeJob[]>([])
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [appliedQuery, setAppliedQuery] = useState('')
  const [error, setError] = useState('')
  const [showSearchInput, setShowSearchInput] = useState(true)
  const refreshInFlight = useRef(false)

  const refreshItems = useCallback(async () => {
    if (refreshInFlight.current) return
    refreshInFlight.current = true
    try {
      const params = new URLSearchParams({ status: 'all', q: appliedQuery })
      const data = await request<{ items: UpgradeJob[] }>(`/api/quality-upgrades?${params}`)
      setAllItems(data.items)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load quality upgrades')
    } finally {
      refreshInFlight.current = false
    }
  }, [appliedQuery])

  async function refresh() {
    await refreshItems()
  }

  useEffect(() => {
    void refreshItems()
  }, [refreshItems])

  useEffect(() => {
    const timer = window.setTimeout(() => setAppliedQuery(query.trim()), 220)
    return () => window.clearTimeout(timer)
  }, [query])

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
      setAllItems(current => current.filter(item => item.id !== job.id))
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
    allItems.forEach(item => { c[item.status] = (c[item.status] || 0) + 1 })
    c.all = allItems.length
    return c
  }, [allItems])

  const visibleFilters = useMemo(() => {
    return FILTERS.filter(({ value, alwaysShow }) => alwaysShow || value === filter || (counts[value] ?? 0) > 0)
  }, [counts, filter])

  const items = useMemo(() => {
    const base = filter === 'all' ? allItems : allItems.filter(item => item.status === filter)
    return base
  }, [allItems, filter])

  return <div className="quality-page quality-page-v2">
    <header className="quality-header quality-header-v2">
      <div className="quality-title-block">
        <span className="eyebrow">Library quality</span>
        <h1>Quality Upgrades</h1>
        <p>Helix uses slskd only to replace eligible tracks with verified higher-quality copies.</p>
      </div>
      <button className="quality-refresh-button" onClick={() => void refresh()}>↻ Refresh</button>
    </header>

    {error ? <div className="error-banner">{error}</div> : null}

    <section className="quality-panel quality-overview-panel">
      <div className="quality-filter-pill-row">
        {visibleFilters.map(({ value, label }) => (
          <button
            key={value}
            className={`quality-filter-pill ${filter === value ? 'active' : ''}`}
            onClick={() => setFilter(value)}
          >
            <span>{label}</span>
            <strong>{counts[value] ?? 0}</strong>
          </button>
        ))}
      </div>

      <div className="quality-toolbar">
        {showSearchInput ? <label className="quality-search-field">
          <span aria-hidden="true">⌕</span>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search artist, track, or album..."
          />
        </label> : null}
        <button className="quality-utility-button" onClick={() => setShowSearchInput(current => !current)}>{showSearchInput ? 'Hide search' : 'Show search'}</button>
      </div>
    </section>

    <section className="quality-panel quality-table-panel">
      <div className="quality-table-head" role="presentation">
        <div>Track</div>
        <div>Status</div>
        <div>From → To</div>
        <div>Details</div>
        <div>Actions</div>
      </div>

      <div className="quality-list quality-list-v2">
        {items.length === 0 ? <div className="quality-empty">No tracks match this filter.</div> : items.map(job => {
          const meta = statusMeta(job)
          const actions = actionsFor(job)
          return <article className="quality-table-row" key={job.id}>
            <div className="quality-cell quality-track-cell" data-label="Track">
              <div className="quality-main">
                <div className="quality-art-wrap">
                  {job.art_url ? <img className="quality-art" src={job.art_url} alt="" loading="lazy" /> : <div className="quality-art quality-art-placeholder" aria-hidden="true">♪</div>}
                </div>
                <div className="quality-track">
                  <strong>{job.title}</strong>
                  <span>{job.artist}{job.album ? ` • ${job.album}` : ''}</span>
                  <div className="quality-track-meta-row">
                    <span className={`quality-status status-${job.status}`}>{meta.title}</span>
                    {job.best_match_score ? <small>{Math.round(job.best_match_score)}% best match</small> : null}
                  </div>
                </div>
              </div>
            </div>

            <div className="quality-cell quality-status-cell" data-label="Status">
              <div className="quality-status-stack">
                <div className="quality-status-line">
                  <span className={`quality-status-dot ${statusDotClass(job.status)}`} />
                  <strong>{meta.title}</strong>
                </div>
                {meta.detail ? <span className={`quality-detail-text ${job.status === 'failed' || job.status === 'no_match' ? 'is-error' : ''}`}>{meta.detail}</span> : null}
              </div>
            </div>

            <div className="quality-cell quality-change-cell" data-label="From → To">
              {job.original?.codec ? (
                hasActualUpgrade(job) ? (
                  <div className="quality-change-pair">
                    <div className="quality-format-block">
                      <span className="quality-format-title">{qualityCodec(job.original)}</span>
                      {qualityDetails(job.original) ? <span className="quality-format-meta">{qualityDetails(job.original)}</span> : null}
                    </div>
                    <span className="quality-arrow">→</span>
                    <div className="quality-format-block">
                      <span className="quality-format-title">{qualityCodec(job.current)}</span>
                      {qualityDetails(job.current) ? <span className="quality-format-meta">{qualityDetails(job.current)}</span> : null}
                    </div>
                  </div>
                ) : (
                  <div className="quality-change-pair quality-change-pending">
                    <div className="quality-format-block">
                      <span className="quality-format-title">{qualityCodec(job.original)}</span>
                      {qualityDetails(job.original) ? <span className="quality-format-meta">{qualityDetails(job.original)}</span> : null}
                    </div>
                    <span className="quality-arrow">→</span>
                    <div className="quality-format-block">
                      <span className="quality-format-title quality-format-muted">Not upgraded yet</span>
                      <span className="quality-format-meta">Current library copy</span>
                    </div>
                  </div>
                )
              ) : (
                <div className="quality-change-pair quality-change-pending">
                  <div className="quality-format-block">
                    <span className="quality-format-title">—</span>
                    <span className="quality-format-meta">Waiting for library copy</span>
                  </div>
                  <span className="quality-arrow">→</span>
                  <div className="quality-format-block">
                    <span className="quality-format-title">—</span>
                    <span className="quality-format-meta">Not searched yet</span>
                  </div>
                </div>
              )}
            </div>

            <div className="quality-cell quality-details-cell" data-label="Details">
              <div className="quality-details-stack">
                <span>{job.completion_source === 'slskd' ? 'Replaced' : meta.title}</span>
                {job.upgraded_at ? <small>{parseBackendDate(job.upgraded_at).toLocaleString()}</small> : null}
                {!job.upgraded_at && job.reverted_at ? <small>{parseBackendDate(job.reverted_at).toLocaleString()}</small> : null}
                {!job.upgraded_at && !job.reverted_at && job.last_search_at ? <small>{parseBackendDate(job.last_search_at).toLocaleString()}</small> : null}
              </div>
            </div>

            <div className="quality-cell quality-actions-cell" data-label="Actions">
              <div className="quality-actions quality-actions-v2">
                {actions.includes('retry') ? <button className="quality-action-button" onClick={() => void action(job, 'retry')}>{['searching', 'waiting_search', 'waiting_peer', 'downloading', 'validating', 'tagging', 'replacing'].includes(job.status) ? 'Restart' : 'Retry'}</button> : null}
                {actions.includes('revert') ? <button className="quality-action-button" onClick={() => void action(job, 'revert')}>Revert</button> : null}
                {actions.includes('enable') ? <button className="quality-action-button" onClick={() => void action(job, 'enable')}>Enable upgrades</button> : null}
                <button className="quality-action-button quality-action-danger" onClick={() => void deleteJob(job)}>Delete</button>
              </div>
            </div>
          </article>
        })}
      </div>
    </section>
  </div>
}
