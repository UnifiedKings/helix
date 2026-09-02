import { useEffect, useState } from 'react'

type Config = {
  slskd_enabled: boolean
  slskd_url: string
  slskd_api_key_configured: boolean
  slskd_downloads_path: string
  slskd_concurrent_searches: number
  slskd_match_threshold: number
  slskd_url_locked: boolean
  slskd_api_key_locked: boolean
  slskd_downloads_path_locked: boolean
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
    try { message = JSON.parse(text).detail ?? message } catch { /* use text */ }
    throw new Error(message)
  }
  return text ? JSON.parse(text) as T : undefined as T
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return <button type="button" className={`settings-toggle ${checked ? 'on' : ''}`} role="switch" aria-checked={checked} onClick={() => onChange(!checked)}><span className="settings-toggle-thumb" /></button>
}

export function SlskdAdminSettingsSection() {
  const [config, setConfig] = useState<Config | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  async function load() {
    try {
      setConfig(await request<Config>('/api/quality-upgrades/admin/config'))
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load slskd settings')
    }
  }

  useEffect(() => { void load() }, [])

  async function save() {
    if (!config) return
    setBusy(true); setMessage(''); setError('')
    const payload: Record<string, unknown> = {
      slskd_enabled: config.slskd_enabled,
      slskd_url: config.slskd_url,
      slskd_downloads_path: config.slskd_downloads_path,
      slskd_concurrent_searches: config.slskd_concurrent_searches,
      slskd_match_threshold: config.slskd_match_threshold,
    }
    if (apiKey) payload.slskd_api_key = apiKey
    try {
      setConfig(await request<Config>('/api/quality-upgrades/admin/config', { method: 'PATCH', body: JSON.stringify(payload) }))
      setApiKey('')
      setMessage('Quality upgrade settings saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save slskd settings')
    } finally {
      setBusy(false)
    }
  }

  async function testConnection() {
    setMessage(''); setError('')
    try {
      const result = await request<{ ok: boolean; error?: string }>('/api/quality-upgrades/admin/test-connection', { method: 'POST', body: '{}' })
      if (result.ok) setMessage('Connected to slskd.')
      else setError(result.error || 'Connection failed')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connection failed')
    }
  }

  return <>
    <div className="settings-section-heading">
      <h2>Quality Upgrades</h2>
      <p>Configure slskd for asynchronous higher-quality replacements. These settings are server-wide.</p>
    </div>
    {error ? <div className="error-banner">{error}</div> : null}
    {message ? <div className="info-banner">{message}</div> : null}
    {!config ? <div className="settings-card"><p>Loading quality upgrade settings…</p></div> : <div className="settings-card">
      <div className="settings-control-row">
        <div><strong>Enable quality upgrades</strong><span>Use slskd in the background to look for verified higher-quality replacements.</span></div>
        <Toggle checked={config.slskd_enabled} onChange={(checked) => setConfig({ ...config, slskd_enabled: checked })} />
      </div>
      <div className="settings-control-row">
        <div><strong>slskd address</strong><span>{config.slskd_url_locked ? 'Configured by SLSKD_URL.' : 'Address of the slskd server.'}</span></div>
        <input disabled={config.slskd_url_locked} value={config.slskd_url} onChange={(e) => setConfig({ ...config, slskd_url: e.target.value })} placeholder="http://slskd:5030" />
      </div>
      <div className="settings-control-row">
        <div><strong>API key</strong><span>{config.slskd_api_key_locked ? 'Configured by SLSKD_API_KEY.' : config.slskd_api_key_configured ? 'Configured; leave blank to keep the current key.' : 'Administrator API key used by Helix.'}</span></div>
        <input type="password" disabled={config.slskd_api_key_locked} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={config.slskd_api_key_configured ? 'Configured; leave blank to keep' : 'API key'} />
      </div>
      <div className="settings-control-row">
        <div><strong>Downloads path</strong><span>{config.slskd_downloads_path_locked ? 'Configured by SLSKD_DOWNLOADS_PATH.' : 'Path inside the Helix container where completed slskd downloads are mounted.'}</span></div>
        <input disabled={config.slskd_downloads_path_locked} value={config.slskd_downloads_path} onChange={(e) => setConfig({ ...config, slskd_downloads_path: e.target.value })} placeholder="/slskd-downloads" />
      </div>
      <div className="settings-control-row">
        <div><strong>Concurrent searches</strong><span>Maximum number of quality-upgrade Soulseek searches Helix may run at once.</span></div>
        <input type="number" min={1} max={3} value={config.slskd_concurrent_searches} onChange={(e) => setConfig({ ...config, slskd_concurrent_searches: Number(e.target.value) })} />
      </div>
      <div className="settings-control-row">
        <div><strong>Minimum match confidence</strong><span>Lowest identity score Helix will accept before considering a Soulseek result eligible.</span></div>
        <input type="number" min={50} max={100} value={config.slskd_match_threshold} onChange={(e) => setConfig({ ...config, slskd_match_threshold: Number(e.target.value) })} />
      </div>
      <div className="settings-page-actions" style={{ marginTop: 16 }}>
        <button className="primary" disabled={busy} onClick={() => void save()}>{busy ? 'Saving…' : 'Save quality settings'}</button>
        <button disabled={busy} onClick={() => void testConnection()}>Test connection</button>
      </div>
    </div>}
  </>
}
