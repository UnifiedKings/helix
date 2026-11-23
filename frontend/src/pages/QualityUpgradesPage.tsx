import { useCallback, useEffect, useMemo, useState } from 'react'
import { QualityProfilesEditor } from '../components/QualityProfilesEditor'
import { QualitySearchLimits } from '../components/QualitySearchLimits'
import '../styles/quality-upgrades.css'
import '../styles/quality-profiles.css'
import '../styles/quality-upgrades-phase2.css'
import '../styles/quality-upgrades-queue-fix.css'
import '../styles/quality-history.css'

type Quality = { codec: string; bitrate: number; sample_rate: number; bit_depth: number }
type Job = { id: string; title: string; artist: string; album: string; art_url?: string; status: string; attempts: number; best_match_score: number; last_error: string; last_search_at?: string | null; next_search_at?: string | null; upgraded_at?: string | null; reverted_at?: string | null; completion_source: string; original: Quality; current: Quality }
type Event = { id: string; event: string; message: string; data_json: string; created_at: string }
type Audit = { job: Job; provenance: string; fingerprinted: boolean; active_search_id: string; events: Event[] }
type Tab = 'overview' | 'profiles' | 'search' | 'history'
type Target = { label: string; profile_name: string }
type BadReport = { job_id: string; reason: string; reported_at: string }
type HistoricalRecord = { kind: 'legacy' | 'profile' | 'not_recorded'; profile: string | null; original: Quality | null; result: Quality | null; completed_at?: string | null }
const filters = ['all', 'pending', 'searching', 'waiting_search', 'waiting_peer', 'no_match', 'upgraded', 'satisfied', 'reverting', 'failed', 'dormant', 'externally_modified', 'reverted']
const busyStatuses = new Set(['searching', 'waiting_search', 'waiting_peer', 'downloading', 'validating', 'tagging', 'replacing', 'reverting'])
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: 'include', ...init, headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) } })
  const text = await response.text()
  if (!response.ok) { let detail = text; try { detail = JSON.parse(text).detail ?? text } catch { /* plain text */ } throw new Error(detail || `HTTP ${response.status}`) }
  return text ? JSON.parse(text) as T : undefined as T
}
function dateTime(value?: string | null) { if (!value) return '—'; const date = new Date(/Z$|[+-]\d\d:?\d\d$/.test(value) ? value : value + 'Z'); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString() }
function quality(value: Quality) { if (!value?.codec) return 'Waiting for library copy'; const bits = [value.codec.toUpperCase()]; if (value.bit_depth) bits.push(`${value.bit_depth}-bit`); if (value.sample_rate) bits.push(`${value.sample_rate / 1000} kHz`); if (value.bitrate && !['flac', 'alac', 'wav', 'aiff', 'aif'].includes(value.codec.toLowerCase())) bits.push(`${Math.round(value.bitrate / 1000)} kbps`); return bits.join(' · ') }
function label(value: string) { return value.replaceAll('_', ' ') }

export function QualityUpgradesPage() {
  const [tab, setTab] = useState<Tab>('overview')
  const [jobs, setJobs] = useState<Job[]>([])
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [audit, setAudit] = useState<Audit | null>(null)
  const [targets, setTargets] = useState<Record<string,Target>>({})
  const [badFiles, setBadFiles] = useState<Record<string,BadReport>>({})
  const [historyRecords, setHistoryRecords] = useState<Record<string,HistoricalRecord>>({})
  const [historyFilter, setHistoryFilter] = useState<'all' | 'legacy' | 'profile'>('all')
  const refresh = useCallback(async () => { try {
    const [data, targetData, badData, historyData] = await Promise.all([
      request<{ items: Job[] }>('/api/quality-upgrades?status=all'),
      request<{ targets: Record<string,Target> }>('/api/quality-upgrades/profile-targets'),
      request<{ items: BadReport[] }>('/api/quality-upgrades/bad-files'),
      request<{ records: Record<string,HistoricalRecord> }>('/api/quality-upgrades/history-records'),
    ])
    setJobs(data.items)
    setTargets(targetData.targets)
    setBadFiles(Object.fromEntries(badData.items.map(item => [item.job_id, item])))
    setHistoryRecords(historyData.records)
    setError('')
  } catch(e) { setError(e instanceof Error ? e.message : String(e)) } }, [])
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => { let ws: WebSocket | undefined; let timeout: number | undefined; let closed = false; const connect = () => { if (closed) return; ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/quality-upgrades`); ws.onmessage = event => { try { if (JSON.parse(event.data).type === 'quality-upgrades.changed') void refresh() } catch { /* ignore */ } }; ws.onclose = () => { if (!closed) timeout = window.setTimeout(connect, 2000) } }; connect(); return () => { closed = true; if (timeout !== undefined) clearTimeout(timeout); ws?.close() } }, [refresh])
  const filtered = useMemo(() => jobs.filter(j => (filter === 'all' || filter === j.status) && (tab !== 'history' || historyFilter === 'all' || historyRecords[j.id]?.kind === historyFilter) && `${j.title} ${j.artist} ${j.album}`.toLowerCase().includes(query.toLowerCase())), [jobs, filter, query, tab, historyFilter, historyRecords])
  const counts = useMemo(() => { const n: Record<string, number> = { all: jobs.length }; jobs.forEach(j => { n[j.status] = (n[j.status] || 0) + 1 }); return n }, [jobs])
  async function action(job: Job, operation: 'retry' | 'enable' | 'revert' | 'delete') {
    if (operation === 'revert' && !window.confirm(`Revert ${job.artist} — ${job.title} to a fresh YTMusic copy? This disables automatic upgrades.`)) return
    if (operation === 'delete' && !window.confirm(`Delete upgrade record for ${job.artist} — ${job.title}? This does not delete the music file.`)) return
    setBusy(true)
    try { await request(`/api/quality-upgrades/${encodeURIComponent(job.id)}${operation === 'delete' ? '' : `/${operation}`}`, { method: operation === 'delete' ? 'DELETE' : 'POST', ...(operation === 'delete' ? {} : { body: '{}' }) }); await refresh() } catch(e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  async function toggleBad(job: Job) {
    if (busy) return
    const report = badFiles[job.id]
    if (report && !window.confirm(`Clear the bad-file report for ${job.artist} — ${job.title}?`)) return
    const reason = report ? '' : window.prompt('Why is this file bad? (corrupted, incorrect recording, incomplete, distorted…)')
    if (!report && !reason?.trim()) return
    setBusy(true)
    try {
      const url = `/api/quality-upgrades/${encodeURIComponent(job.id)}/${report ? 'clear-bad' : 'mark-bad'}`
      try {
        await request(url, { method: 'POST', body: JSON.stringify(report ? {} : { reason }) })
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error)
        if (report || !detail.startsWith('LEGACY_CONFIRM_REQUIRED:')) throw error
        const confirmed = window.confirm(
          `${job.artist} — ${job.title}\n\nHelix has an older upgrade record but no historical file fingerprint.\n\nOnly confirm if this exact music file was added by Helix and has not been manually replaced. Helix will record its current fingerprint and queue a validated replacement. Continue?`
        )
        if (!confirmed) return
        await request(url, { method: 'POST', body: JSON.stringify({ reason, confirm_legacy_ownership: true }) })
      }
      await refresh()
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally { setBusy(false) }
  }
  async function openAudit(job: Job) { setBusy(true); try { setAudit(await request<Audit>(`/api/quality-upgrades/${encodeURIComponent(job.id)}/events`)) } catch(e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  return <div className="quality-page qp-page">
    <header className="quality-header quality-header-v2 qp-page-header"><div className="quality-title-block"><span className="eyebrow">Library quality</span><h1>Quality &amp; Upgrades</h1><p>Manage quality profiles, upgrade behavior, and file replacement.</p></div><button className="quality-refresh-button" onClick={() => void refresh()}>↻ Refresh</button></header>
    <nav className="qp-tabs" aria-label="Quality upgrades sections">{([['overview','Overview'],['profiles','Quality Profiles'],['search','Search & Limits'],['history','History']] as [Tab,string][]).map(([id,name]) => <button type="button" key={id} className={tab === id ? 'active' : ''} aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}>{name}</button>)}</nav>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {tab === 'profiles' && <QualityProfilesEditor />}
    {tab === 'overview' && <section className="qp-overview" aria-label="Upgrade overview"><div className="qp-summary"><div><small>Upgrade jobs</small><strong>{jobs.length}</strong></div><div><small>Active operations</small><strong>{jobs.filter(j => busyStatuses.has(j.status)).length}</strong></div><div><small>Upgraded</small><strong>{counts.upgraded || 0}</strong></div><div><small>Needs attention</small><strong>{(counts.failed || 0) + (counts.no_match || 0)}</strong></div></div></section>}
    {(tab === 'overview' || tab === 'history') && <section className="quality-panel quality-table-panel qp-job-panel"><div className="qp-queue-toolbar"><div className="qp-queue-title"><h2>{tab === 'history' ? 'Upgrade history & records' : 'Upgrade queue'}</h2>{tab === 'overview' ? <p>Tracks follow their quality profiles. Bad-file replacements take priority.</p> : <p>Completed upgrades show the policy recorded at the time, not today's target.</p>}</div>{tab === 'history' && <select className="qp-history-filter" aria-label="Historical policy filter" value={historyFilter} onChange={e => setHistoryFilter(e.target.value as typeof historyFilter)}><option value="all">All policies</option><option value="legacy">Legacy upgrades</option><option value="profile">Profile-managed</option></select>}<input value={query} onChange={e => setQuery(e.target.value)} aria-label="Search artist, album, or track" placeholder="Search tracks…" /></div><div className="quality-filter-pill-row">{filters.filter(f => f === 'all' || f === filter || (counts[f] || 0) > 0 || ['pending','searching','upgraded','failed'].includes(f)).map(f => <button key={f} type="button" className={`quality-filter-pill ${filter === f ? 'active' : ''}`} onClick={() => setFilter(f)}>{label(f)} <strong>{counts[f] || 0}</strong></button>)}</div><div className="quality-table-head qp-table-head" role="presentation"><div>Track</div><div>Status</div><div>{tab === 'history' ? 'Original → Result' : 'Current → Target'}</div><div>{tab === 'history' ? 'Completed' : 'Next search'}</div><div>Actions</div></div><div className="quality-list quality-list-v2">{filtered.length === 0 && <p className="quality-empty">No tracks match this filter.</p>}{filtered.map(job => <article className="quality-table-row" key={job.id}><div className="quality-cell quality-track-cell" data-label="Track"><div className="quality-main"><div className="quality-art-wrap">{job.art_url ? <img className="quality-art" src={job.art_url} alt="" loading="lazy" /> : <div className="quality-art quality-art-placeholder">♪</div>}</div><div className="quality-track"><strong>{job.title}</strong><span>{job.artist}{job.album ? ` · ${job.album}` : ''}</span>{tab === 'history' && historyRecords[job.id]?.kind === 'legacy' && <span className="qp-legacy-label">Legacy · Previous upgrade rules</span>}{tab === 'history' && historyRecords[job.id]?.kind === 'profile' && <span className="qp-managed-label">Profile · {historyRecords[job.id].profile}</span>}</div></div></div><div className="quality-cell quality-status-cell" data-label="Status"><span className={`quality-status status-${job.status}`}>{badFiles[job.id] ? "Bad file · " : ""}{label(job.status)}</span>{job.last_error && <small className="qp-job-error" title={job.last_error}>{job.last_error}</small>}</div><div className="quality-cell quality-change-cell" data-label="Current → Target"><div className="quality-change-pair qp-quality-values"><span className="qp-current-quality" title={quality(tab === 'history' ? (historyRecords[job.id]?.original || job.original) : job.current)}>{quality(tab === 'history' ? (historyRecords[job.id]?.original || job.original) : job.current)}</span><span className="quality-arrow" aria-hidden="true">→</span><span className="qp-target-quality" title={tab === 'history' ? 'Quality recorded at completion (or latest known for legacy records)' : (targets[job.id]?.profile_name ? `${targets[job.id].profile_name}: ${targets[job.id].label}` : 'Assigned profile')}>{tab === 'history' ? quality(historyRecords[job.id]?.result || job.current) : (targets[job.id]?.label || 'No target configured')}</span></div></div><div className="quality-cell qp-next-search" data-label="Next search">{dateTime(tab === 'history' ? (historyRecords[job.id]?.completed_at || job.upgraded_at) : job.next_search_at)}</div><div className="quality-cell quality-actions-cell" data-label="Actions">
              <div className="qp-row-actions">
                <button type="button" className="qp-details-button" disabled={busy} onClick={() => void openAudit(job)}>Details</button>
                <details className="qp-action-menu">
                  <summary aria-label={`More actions for ${job.title}`} title={`More actions for ${job.title}`}>•••</summary>
                  <div className="qp-action-popover" onClick={e => { const detail = (e.target as HTMLElement).closest('details'); if (detail) detail.open = false }}>
                    {job.status !== 'upgraded' && job.status !== 'reverting' && <button type="button" disabled={busy} onClick={() => void action(job, ['satisfied','reverted','externally_modified'].includes(job.status) ? 'enable' : 'retry')}>{['satisfied','reverted','externally_modified'].includes(job.status) ? 'Enable upgrades' : 'Retry search'}</button>}
                    {job.status === 'upgraded' && <button type="button" disabled={busy} onClick={() => void action(job, 'revert')}>Revert upgrade</button>}
                    <button type="button" disabled={busy} onClick={() => void toggleBad(job)}>{badFiles[job.id] ? 'Clear bad-file report' : 'Mark file as bad'}</button>
                    {job.status !== 'reverting' && <button type="button" className="qp-destructive" disabled={busy} onClick={() => void action(job, 'delete')}>Delete upgrade record</button>}
                  </div>
                </details>
              </div>
            </div></article>)}</div></section>}
    {tab === 'search' && <QualitySearchLimits />}
    {audit && <div className="quality-audit-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) setAudit(null) }}><aside className="quality-audit-panel" role="dialog" aria-modal="true" aria-label={`Upgrade details for ${audit.job.title}`}><div className="quality-audit-header"><div><h2>{audit.job.title}</h2><p>{audit.job.artist}</p></div><button className="quality-action-button" onClick={() => setAudit(null)}>Close</button></div><div className="quality-audit-summary"><div><span>Ownership</span><strong>{audit.provenance}</strong></div><div><span>Fingerprint</span><strong>{audit.fingerprinted ? 'Recorded' : 'Pending'}</strong></div><div><span>Active search</span><strong>{audit.active_search_id || 'None'}</strong></div></div><div className="quality-audit-events">{audit.events.map(event => <div key={event.id} className="quality-audit-event"><time>{dateTime(event.created_at)}</time><strong>{label(event.event)}</strong><p>{event.message}</p></div>)}</div></aside></div>}
  </div>
}
