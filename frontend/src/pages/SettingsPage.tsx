import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'

function parseSettingValue(raw: string, original: unknown): unknown {
  if (typeof original === 'boolean') return raw === 'true'
  if (typeof original === 'number') {
    const n = Number(raw)
    return Number.isFinite(n) ? n : original
  }
  if (raw === 'true') return true
  if (raw === 'false') return false
  return raw
}

function settingType(value: unknown) {
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number') return 'number'
  return 'text'
}

const SETTING_GROUPS: Array<{ title: string; keys: string[] }> = [
  { title: 'Subsonic', keys: ['subsonic_base_url', 'subsonic_username', 'subsonic_password', 'subsonic_client_name', 'subsonic_api_version', 'subsonic_timeout_s'] },
  { title: 'Playback', keys: ['player_max_queue_items', 'player_omit_missing', 'listen_history_limit'] },
  { title: 'Fulfillment', keys: ['fulfillment_library_subfolder', 'fulfillment_tag_comment', 'fulfillment_first_play_timeout_seconds', 'fulfillment_version_preference'] },
  { title: 'Search', keys: ['search_default_country', 'search_hide_non_official', 'search_prefer_original_release', 'search_hide_tracks_without_art', 'search_cache_ttl_seconds'] },
  { title: 'MusicBrainz', keys: ['musicbrainz_min_interval_ms', 'musicbrainz_user_agent'] },
]

export function SettingsPage() {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)

  const groupedKeys = useMemo(() => new Set(SETTING_GROUPS.flatMap((group) => group.keys)), [])
  const extraKeys = useMemo(() => Object.keys(settings).filter((key) => !groupedKeys.has(key)).sort(), [settings, groupedKeys])

  async function load() {
    try {
      const [healthRes, settingsRes] = await Promise.all([api.health(), api.adminSettings()])
      setHealth(healthRes)
      setSettings(settingsRes)
      setDraft(Object.fromEntries(Object.entries(settingsRes).map(([key, value]) => [key, typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')])))
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load settings')
    }
  }

  useEffect(() => { void load() }, [])

  async function saveSettings() {
    setSaving(true)
    setError('')
    setStatus('')
    try {
      const patch: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(draft)) {
        const original = settings[key]
        const parsed = parseSettingValue(value, original)
        if (parsed !== original) patch[key] = parsed
      }
      const updated = await api.updateAdminSettings(patch)
      setSettings(updated)
      setDraft(Object.fromEntries(Object.entries(updated).map(([key, value]) => [key, typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')])))
      setStatus(Object.keys(patch).length ? 'Settings saved.' : 'No changes to save.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save settings')
    } finally {
      setSaving(false)
    }
  }

  function renderSetting(key: string) {
    if (!(key in settings)) return null
    const value = settings[key]
    const type = settingType(value)
    const isSecret = key.toLowerCase().includes('password')
    return (
      <label className="setting-row" key={key}>
        <span><strong>{key}</strong><small>{type}</small></span>
        {type === 'boolean' ? (
          <select value={draft[key] ?? String(value)} onChange={(event) => setDraft((prev) => ({ ...prev, [key]: event.target.value }))}>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        ) : (
          <input type={isSecret ? 'password' : type === 'number' ? 'number' : 'text'} value={draft[key] ?? ''} onChange={(event) => setDraft((prev) => ({ ...prev, [key]: event.target.value }))} />
        )}
      </label>
    )
  }

  return (
    <div className="page-stack settings-editor-page">
      <section className="detail-hero compact-detail-hero">
        <div className="detail-copy">
          <span className="eyebrow">Admin</span>
          <h1>Settings</h1>
          <p className="muted">Edit Helix backend settings directly. Changes are saved through the admin settings API.</p>
        </div>
        <div className="detail-actions"><button className="primary" disabled={saving} onClick={() => void saveSettings()}>{saving ? 'Saving…' : 'Save settings'}</button><button onClick={() => void load()}>Reload</button></div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}

      <section className="panel settings-health-panel">
        <h2>Backend health</h2>
        <pre>{JSON.stringify(health, null, 2)}</pre>
      </section>

      {SETTING_GROUPS.map((group) => (
        <section className="panel settings-group" key={group.title}>
          <h2>{group.title}</h2>
          <div className="settings-grid">{group.keys.map(renderSetting)}</div>
        </section>
      ))}

      {extraKeys.length ? (
        <section className="panel settings-group">
          <h2>Other settings</h2>
          <div className="settings-grid">{extraKeys.map(renderSetting)}</div>
        </section>
      ) : null}
    </div>
  )
}
