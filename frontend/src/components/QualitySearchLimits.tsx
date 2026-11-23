import { useCallback, useEffect, useState } from 'react'
import '../styles/quality-search-limits.css'

type Limits = { used_hourly: number; used_daily: number; wait_seconds: number; max_hourly: number; max_daily: number; concurrent_searches: number }
type Draft = Pick<Limits, 'max_hourly' | 'max_daily' | 'concurrent_searches'>

async function call<T>(method: 'GET' | 'PATCH', data?: Draft): Promise<T> {
  const response = await fetch('/api/quality-upgrades/admin/search-limits', {
    method, credentials: 'include',
    ...(data ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      quality_search_max_hourly: data.max_hourly, quality_search_max_daily: data.max_daily,
      slskd_concurrent_searches: data.concurrent_searches,
    }) } : {}),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail || `Search limits request failed (${response.status})`)
  return body as T
}

export function QualitySearchLimits() {
  const [limits, setLimits] = useState<Limits | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = useCallback(async () => {
    try {
      const value = await call<Limits>('GET')
      setLimits(value)
      setDraft(current => current ?? { max_hourly: value.max_hourly, max_daily: value.max_daily, concurrent_searches: value.concurrent_searches })
      setError('')
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }, [])
  useEffect(() => {
    void reload()
    const timer = window.setInterval(() => void reload(), 30000)
    return () => window.clearInterval(timer)
  }, [reload])

  async function save() {
    if (!draft) return
    setBusy(true)
    try {
      const saved = await call<Limits>('PATCH', draft)
      setLimits(saved)
      setDraft({ max_hourly: saved.max_hourly, max_daily: saved.max_daily, concurrent_searches: saved.concurrent_searches })
      setError('')
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  const dirty = Boolean(limits && draft && (draft.max_hourly !== limits.max_hourly || draft.max_daily !== limits.max_daily || draft.concurrent_searches !== limits.concurrent_searches))
  return <section className="quality-panel qsl-panel">
    <div className="qsl-heading"><div><h2>Search &amp; Limits</h2><p>These limits count every new Soulseek upgrade search request, not just finished upgrade jobs. Counters persist across restarts.</p></div><button type="button" onClick={() => void reload()} disabled={busy}>Refresh</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {limits && <div className="qsl-metrics">
      <div><small>Past hour</small><strong>{limits.used_hourly} / {limits.max_hourly || 'Unlimited'}</strong>{limits.max_hourly > 0 && <progress max={limits.max_hourly} value={limits.used_hourly} />}</div>
      <div><small>Past 24 hours</small><strong>{limits.used_daily} / {limits.max_daily || 'Unlimited'}</strong>{limits.max_daily > 0 && <progress max={limits.max_daily} value={limits.used_daily} />}</div>
      <div><small>Budget status</small><strong>{limits.wait_seconds > 0 ? 'Cooling down' : 'Available'}</strong><span>{limits.wait_seconds > 0 ? `Next slot in about ${Math.ceil(limits.wait_seconds / 60)} min` : 'New searches allowed'}</span></div>
    </div>}
    {draft ? <div className="qsl-form">
      <label>Maximum searches per hour<select value={draft.max_hourly} onChange={e => setDraft({ ...draft, max_hourly: Number(e.target.value) })}>{[0,1,2,5,10,15,20,30,60,120,240].map(n => <option key={n} value={n}>{n === 0 ? 'Unlimited' : n}</option>)}</select></label>
      <label>Maximum searches per 24 hours<select value={draft.max_daily} onChange={e => setDraft({ ...draft, max_daily: Number(e.target.value) })}>{[0,5,10,20,30,50,75,100,150,200,300,500,1000,2000].map(n => <option key={n} value={n}>{n === 0 ? 'Unlimited' : n}</option>)}</select></label>
      <label>Concurrent upgrade jobs<select value={draft.concurrent_searches} onChange={e => setDraft({ ...draft, concurrent_searches: Number(e.target.value) })}>{[1,2,3].map(n => <option key={n} value={n}>{n === 0 ? 'Unlimited' : n}</option>)}</select></label>
    </div> : <p>Loading scheduler configuration…</p>}
    <div className="qsl-footer"><p>Existing per-track no-match retries remain 1, 7, then 30 days, followed by dormancy. Completed searches and download transfers are not interrupted when a budget is reached.</p><button type="button" className="qsl-save" onClick={() => void save()} disabled={!dirty || busy}>{busy ? 'Saving…' : 'Save changes'}</button></div>
  </section>
}
