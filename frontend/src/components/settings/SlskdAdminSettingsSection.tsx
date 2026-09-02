import { useEffect, useState } from 'react'

type Config = {
  slskd_enabled: boolean
  slskd_url: string
  slskd_api_key_configured: boolean
  slskd_downloads_path: string
  slskd_concurrent_searches: number
  slskd_match_threshold: number
  quality_upgrade_lossless_only: boolean
  quality_upgrade_min_sample_rate: number
  quality_upgrade_min_bit_depth: number
  quality_upgrade_replace_lossless: boolean
  quality_upgrade_management_scope: string
  quality_upgrade_future_adoption_supported: boolean
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
  const [connectionStatus, setConnectionStatus] = useState<'checking' | 'connected' | 'disconnected'>('checking')

  async function load() {
    try {
      setConfig(await request<Config>('/api/quality-upgrades/admin/config'))
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load slskd settings')
    }
  }

  useEffect(() => {
    void load()
    void checkConnection(false)

    const interval = window.setInterval(() => {
      void checkConnection(false)
    }, 30000)

    return () => window.clearInterval(interval)
  }, [])

  async function save() {
    if (!config) return
    setBusy(true); setMessage(''); setError('')
    const payload: Record<string, unknown> = {
      slskd_enabled: config.slskd_enabled,
      slskd_url: config.slskd_url,
      slskd_downloads_path: config.slskd_downloads_path,
      slskd_concurrent_searches: config.slskd_concurrent_searches,
      slskd_match_threshold: config.slskd_match_threshold,
      quality_upgrade_lossless_only: config.quality_upgrade_lossless_only,
      quality_upgrade_min_sample_rate: config.quality_upgrade_min_sample_rate,
      quality_upgrade_min_bit_depth: config.quality_upgrade_min_bit_depth,
      quality_upgrade_replace_lossless: config.quality_upgrade_replace_lossless,
    }
    if (apiKey) payload.slskd_api_key = apiKey
    try {
      setConfig(await request<Config>('/api/quality-upgrades/admin/config', { method: 'PATCH', body: JSON.stringify(payload) }))
      setApiKey('')
      setMessage('Quality upgrade settings saved.')
      void checkConnection(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save slskd settings')
    } finally {
      setBusy(false)
    }
  }

  async function checkConnection(announce: boolean) {
    setConnectionStatus('checking')
    if (announce) {
      setMessage('')
      setError('')
    }

    try {
      const result = await request<{ ok: boolean; error?: string }>('/api/quality-upgrades/admin/test-connection', { method: 'POST', body: '{}' })
      if (result.ok) {
        setConnectionStatus('connected')
        if (announce) setMessage('Connected to slskd.')
      } else {
        setConnectionStatus('disconnected')
        if (announce) setError(result.error || 'Connection failed')
      }
    } catch (err) {
      setConnectionStatus('disconnected')
      if (announce) setError(err instanceof Error ? err.message : 'Connection failed')
    }
  }

  async function testConnection() {
    await checkConnection(true)
  }

  return <>
    <div className="settings-section-heading">
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0 }}>Quality Upgrades</h2>
        <span
          role="status"
          aria-label={`slskd ${connectionStatus}`}
          title={
            connectionStatus === 'connected'
              ? 'Helix is connected to slskd'
              : connectionStatus === 'disconnected'
                ? 'Helix cannot reach slskd'
                : 'Checking slskd connection…'
          }
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.42rem',
            minHeight: 26,
            padding: '0.24rem 0.55rem',
            border: '1px solid color-mix(in srgb, var(--text) 10%, transparent)',
            borderRadius: 6,
            background: 'color-mix(in srgb, var(--surface-raised) 72%, transparent)',
            color: 'var(--muted)',
            fontSize: '0.74rem',
            fontWeight: 650,
            lineHeight: 1,
            whiteSpace: 'nowrap',
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: 'inline-block',
              width: 8,
              height: 8,
              flex: '0 0 8px',
              borderRadius: '50%',
              background:
                connectionStatus === 'connected'
                  ? 'var(--good)'
                  : connectionStatus === 'disconnected'
                    ? 'var(--danger, #e35d6a)'
                    : 'var(--muted)',
              boxShadow:
                connectionStatus === 'connected'
                  ? '0 0 0 2px color-mix(in srgb, var(--good) 16%, transparent), 0 0 8px color-mix(in srgb, var(--good) 42%, transparent)'
                  : connectionStatus === 'disconnected'
                    ? '0 0 0 2px color-mix(in srgb, var(--danger, #e35d6a) 14%, transparent)'
                    : 'none',
              transition: 'background .18s ease, box-shadow .18s ease',
            }}
          />
          <span>
            slskd {connectionStatus === 'connected'
              ? 'Connected'
              : connectionStatus === 'disconnected'
                ? 'Disconnected'
                : 'Checking…'}
          </span>
        </span>
      </div>
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
      <div className="settings-control-row">
        <div><strong>Lossless upgrades only</strong><span>Require a verified lossless result before Helix will replace the current file.</span></div>
        <Toggle checked={config.quality_upgrade_lossless_only} onChange={(checked) => setConfig({ ...config, quality_upgrade_lossless_only: checked })} />
      </div>
      <div className="settings-control-row">
        <div><strong>Minimum sample rate</strong><span>Reject known candidates below this sample rate. Unknown Soulseek metadata is validated after download.</span></div>
        <input type="number" min={8000} max={384000} step={1000} value={config.quality_upgrade_min_sample_rate} onChange={(e) => setConfig({ ...config, quality_upgrade_min_sample_rate: Number(e.target.value) })} />
      </div>
      <div className="settings-control-row">
        <div><strong>Minimum bit depth</strong><span>Reject known candidates below this bit depth.</span></div>
        <input type="number" min={8} max={32} value={config.quality_upgrade_min_bit_depth} onChange={(e) => setConfig({ ...config, quality_upgrade_min_bit_depth: Number(e.target.value) })} />
      </div>
      <div className="settings-control-row">
        <div><strong>Upgrade existing lossless files</strong><span>When enabled, Helix-owned lossless files may be replaced only by a strictly higher-ranked lossless candidate.</span></div>
        <Toggle checked={config.quality_upgrade_replace_lossless} onChange={(checked) => setConfig({ ...config, quality_upgrade_replace_lossless: checked })} />
      </div>
      <div className="settings-control-row">
        <div><strong>Managed library scope</strong><span>Automatic replacement is currently limited to tracks originally added by Helix. The provenance model leaves room for a future opt-in adoption workflow.</span></div>
        <strong>Helix-added tracks only</strong>
      </div>
      <div
        className="settings-page-actions"
        style={{
          marginTop: 0,
          padding: '1rem',
          gap: '0.65rem',
          borderTop: '1px solid color-mix(in srgb, var(--text) 6.5%, transparent)',
        }}
      >
        <button className="primary" disabled={busy} onClick={() => void save()}>{busy ? 'Saving…' : 'Save quality settings'}</button>
        <button disabled={busy || connectionStatus === 'checking'} onClick={() => void testConnection()}>
          {connectionStatus === 'checking' ? 'Checking…' : 'Test connection'}
        </button>
      </div>
    </div>}
  </>
}
