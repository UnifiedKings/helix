import { FormEvent, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { AdminUser } from '../api/types'

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

const HIDDEN_SETTING_KEYS = new Set([
  'artist_images_enable_wikipedia',
  'artist_images_fallback_to_album_art',
  'image_cache_max_mb',
  'image_cache_thumb_px',
  'image_cache_ttl_days',
  'image_proxy_enabled',
  'search_cache_ttl_seconds',
  'search_hide_tracks_without_art',
  'subsonic_configured',
  'subsonic_password_configured',
])

const SETTING_GROUPS: Array<{ title: string; keys: string[] }> = [
  { title: 'Subsonic', keys: ['subsonic_base_url', 'subsonic_username', 'subsonic_password', 'subsonic_client_name', 'subsonic_api_version', 'subsonic_timeout_s'] },
  { title: 'Playback', keys: ['player_max_queue_items', 'player_omit_missing', 'listen_history_limit'] },
  { title: 'Fulfillment', keys: ['fulfillment_library_subfolder', 'fulfillment_tag_comment', 'fulfillment_first_play_timeout_seconds', 'fulfillment_version_preference'] },
  { title: 'Search', keys: ['search_default_country', 'search_hide_non_official', 'search_prefer_original_release'] },
  { title: 'MusicBrainz', keys: ['musicbrainz_min_interval_ms', 'musicbrainz_user_agent'] },
]

export function SettingsPage() {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [userDraft, setUserDraft] = useState({ username: '', password: '', role: 'user' as 'admin' | 'user' })
  const [creatingUser, setCreatingUser] = useState(false)
  const [updatingUserId, setUpdatingUserId] = useState('')

  const groupedKeys = useMemo(() => new Set(SETTING_GROUPS.flatMap((group) => group.keys)), [])
  const extraKeys = useMemo(
    () => Object.keys(settings).filter((key) => !groupedKeys.has(key) && !HIDDEN_SETTING_KEYS.has(key)).sort(),
    [settings, groupedKeys],
  )

  async function load() {
    try {
      const [healthRes, settingsRes, usersRes] = await Promise.all([api.health(), api.adminSettings(), api.adminUsers()])
      setHealth(healthRes)
      setSettings(settingsRes)
      setUsers(usersRes)
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

  async function createUser(event: FormEvent) {
    event.preventDefault()
    const username = userDraft.username.trim()
    if (!username || !userDraft.password || creatingUser) return
    setCreatingUser(true)
    setError('')
    setStatus('')
    try {
      const created = await api.createAdminUser({ username, password: userDraft.password, role: userDraft.role })
      setUsers((current) => [created, ...current])
      setUserDraft({ username: '', password: '', role: 'user' })
      setStatus(`Created user: ${created.username}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create user')
    } finally {
      setCreatingUser(false)
    }
  }

  async function updateUser(user: AdminUser, patch: { is_active?: boolean; role?: 'admin' | 'user' }) {
    setUpdatingUserId(user.id)
    setError('')
    setStatus('')
    try {
      const updated = await api.updateAdminUser(user.id, patch)
      setUsers((current) => current.map((item) => item.id === updated.id ? updated : item))
      setStatus(`Updated user: ${updated.username}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update user')
    } finally {
      setUpdatingUserId('')
    }
  }

  function renderSetting(key: string) {
    if (HIDDEN_SETTING_KEYS.has(key) || !(key in settings)) return null
    const value = settings[key]
    const type = settingType(value)
    const isSecret = key.toLowerCase().includes('password')
    return (
      <label className="setting-row" key={key}>
        <span><strong>{key}</strong><small>{isSecret ? 'secret; leave blank to keep existing value' : type}</small></span>
        {type === 'boolean' ? (
          <select value={draft[key] ?? String(value)} onChange={(event) => setDraft((prev) => ({ ...prev, [key]: event.target.value }))}>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        ) : (
          <input type={isSecret ? 'password' : type === 'number' ? 'number' : 'text'} value={draft[key] ?? ''} placeholder={isSecret && settings[`${key}_configured`] ? 'Configured; leave blank to keep' : ''} onChange={(event) => setDraft((prev) => ({ ...prev, [key]: event.target.value }))} />
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

      <section className="panel settings-group admin-users-panel">
        <div className="section-heading">
          <div>
            <h2>Users</h2>
            <span className="muted">Create accounts and manage whether users are active.</span>
          </div>
        </div>

        <form className="admin-user-create-form" onSubmit={createUser}>
          <label className="setting-row">
            <span><strong>Username</strong><small>3–64 chars</small></span>
            <input value={userDraft.username} onChange={(event) => setUserDraft((prev) => ({ ...prev, username: event.target.value }))} placeholder="new-user" />
          </label>
          <label className="setting-row">
            <span><strong>Password</strong><small>min 8 chars</small></span>
            <input type="password" value={userDraft.password} onChange={(event) => setUserDraft((prev) => ({ ...prev, password: event.target.value }))} placeholder="Temporary password" />
          </label>
          <label className="setting-row">
            <span><strong>Role</strong><small>access</small></span>
            <select value={userDraft.role} onChange={(event) => setUserDraft((prev) => ({ ...prev, role: event.target.value as 'admin' | 'user' }))}>
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <div className="admin-user-create-actions">
            <button className="primary" type="submit" disabled={creatingUser || !userDraft.username.trim() || userDraft.password.length < 8}>
              {creatingUser ? 'Creating…' : 'Create user'}
            </button>
          </div>
        </form>

        <div className="admin-user-list">
          {users.map((user) => (
            <div className="admin-user-row" key={user.id}>
              <div>
                <strong>{user.username}</strong>
                <span className="muted">{user.role} · {user.is_active ? 'active' : 'disabled'}</span>
              </div>
              <select
                value={user.role}
                disabled={updatingUserId === user.id}
                onChange={(event) => void updateUser(user, { role: event.target.value as 'admin' | 'user' })}
                aria-label={`Role for ${user.username}`}
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
              <button
                type="button"
                className={user.is_active ? 'ghost danger' : 'ghost'}
                disabled={updatingUserId === user.id}
                onClick={() => void updateUser(user, { is_active: !user.is_active })}
              >
                {user.is_active ? 'Disable' : 'Enable'}
              </button>
            </div>
          ))}
        </div>
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
