import { FormEvent, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { AdminUser } from '../api/types'
import { SlskdAdminSettingsSection } from '../components/settings/SlskdAdminSettingsSection'
import '../styles/account-management.css'

type AdminSection = 'overview' | 'users' | 'library' | 'quality' | 'playback' | 'downloads' | 'search' | 'advanced'

type QualityAdminNotification = { id: string; title: string; body: string; created_at: string }

const SECTIONS: Array<[AdminSection, string]> = [
  ['overview', 'Overview'], ['users', 'Users'], ['library', 'Library & Subsonic'], ['quality', 'Quality Upgrades'],
  ['playback', 'Playback'], ['downloads', 'Downloads & Prefetch'], ['search', 'Search & Catalog'], ['advanced', 'Advanced'],
]

const DEFINITIONS: Record<string, { label: string; description: string; group: AdminSection; kind?: 'secret' | 'boolean' | 'number' }> = {
  subsonic_base_url: { label: 'Server URL', description: 'Address of the Subsonic-compatible music server.', group: 'library' },
  subsonic_username: { label: 'Username', description: 'Account Helix uses to access the music library.', group: 'library' },
  subsonic_password: { label: 'Password', description: 'Leave blank to keep the currently configured password.', group: 'library', kind: 'secret' },
  subsonic_timeout_s: { label: 'Request timeout', description: 'Maximum wait for Subsonic requests, in seconds.', group: 'library', kind: 'number' },
  allow_all_users_subsonic_import: { label: 'Allow all users to add to Subsonic', description: 'Administrators always have access. When disabled, normal users must be granted permission individually.', group: 'library', kind: 'boolean' },
  subsonic_client_name: { label: 'Client name', description: 'Advanced Subsonic API client identifier.', group: 'advanced' },
  subsonic_api_version: { label: 'API version', description: 'Advanced Subsonic protocol version.', group: 'advanced' },
  station_queue_ahead_max: { label: 'Maximum station tracks ahead', description: 'Hard ceiling for each user’s personal station queue-ahead preference.', group: 'playback', kind: 'number' },
  download_prefetch_ahead: { label: 'Download tracks ahead', description: 'How many upcoming queue items Helix proactively downloads.', group: 'downloads', kind: 'number' },
  listen_history_retention: { label: 'History rows retained per user', description: 'Global storage limit for playback history.', group: 'playback', kind: 'number' },
  search_default_country: { label: 'Preferred release country', description: 'Country Helix favors when selecting representative releases.', group: 'search' },
  search_hide_non_official: { label: 'Hide unofficial releases', description: 'Hide unofficial releases when alternate versions are listed.', group: 'search', kind: 'boolean' },
  search_prefer_original_release: { label: 'Prefer original release', description: 'Favor the earliest official release over the preferred country.', group: 'search', kind: 'boolean' },
}

const OBSOLETE_CANDIDATES = new Set(['player_max_queue_items', 'player_omit_missing', 'fulfillment_library_subfolder', 'fulfillment_tag_comment', 'fulfillment_first_play_timeout_seconds', 'fulfillment_version_preference', 'musicbrainz_min_interval_ms', 'musicbrainz_user_agent'])

function Toggle({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return <button type="button" className={`settings-toggle ${checked ? 'on' : ''}`} role="switch" aria-checked={checked} onClick={() => onChange(!checked)}><span className="settings-toggle-thumb" /></button>
}

async function accountRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
  })
  const text = await response.text()
  if (!response.ok) {
    let detail = text || `${response.status} ${response.statusText}`
    try { detail = JSON.parse(text).detail || detail } catch { /* use text */ }
    throw new Error(detail)
  }
  return text ? JSON.parse(text) as T : undefined as T
}

export function AdminSettingsPage() {
  const [section, setSection] = useState<AdminSection>('overview')
  const [health, setHealth] = useState<Record<string, unknown>>({})
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [users, setUsers] = useState<AdminUser[]>([])
  const [currentUserId, setCurrentUserId] = useState('')
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'user' as 'user' | 'admin' })
  const [passwordUserId, setPasswordUserId] = useState('')
  const [resetPassword, setResetPassword] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)
  const [accountBusy, setAccountBusy] = useState('')
  const [qualityNotifications, setQualityNotifications] = useState<QualityAdminNotification[]>([])

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(settings), [draft, settings])

  async function load() {
    try {
      const [nextHealth, nextSettings, nextUsers, me, notifications] = await Promise.all([
        api.health(),
        api.adminSettings(),
        api.adminUsers(),
        api.me(),
        accountRequest<{ items: QualityAdminNotification[] }>('/api/quality-upgrades/admin/notifications').catch(() => ({ items: [] })),
      ])
      setHealth(nextHealth); setSettings(nextSettings); setDraft(nextSettings); setUsers(nextUsers); setCurrentUserId(me.id); setQualityNotifications(notifications.items ?? []); setError('')
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not load admin settings') }
  }
  useEffect(() => { void load() }, [])

  async function save() {
    setSaving(true); setError(''); setStatus('')
    try {
      const patch: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(draft)) if (value !== settings[key]) patch[key] = value
      const next = await api.updateAdminSettings(patch)
      setSettings(next); setDraft(next); setStatus(Object.keys(patch).length ? 'Global settings saved.' : 'No changes to save.')
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save admin settings') }
    finally { setSaving(false) }
  }

  async function createUser(event: FormEvent) {
    event.preventDefault(); setError(''); setStatus('')
    try {
      const created = await api.createAdminUser(newUser)
      setUsers((current) => [created, ...current]); setNewUser({ username: '', password: '', role: 'user' }); setStatus(`Created ${created.username}.`)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not create user') }
  }

  async function updateUser(user: AdminUser, patch: { is_active?: boolean; role?: 'admin' | 'user'; subsonic_import_override?: boolean }) {
    setError(''); setStatus('')
    try {
      const next = await api.updateAdminUser(user.id, patch)
      setUsers((current) => current.map((item) => item.id === next.id ? next : item))
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not update user') }
  }

  async function submitPasswordReset(user: AdminUser) {
    if (resetPassword.length < 8) return
    setAccountBusy(user.id); setError(''); setStatus('')
    try {
      await accountRequest<{ ok: boolean }>(`/admin/users/${encodeURIComponent(user.id)}/reset-password`, {
        method: 'POST', body: JSON.stringify({ new_password: resetPassword }),
      })
      setPasswordUserId(''); setResetPassword('')
      setStatus(`Password reset for ${user.username}. Existing sessions were signed out.`)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not reset password') }
    finally { setAccountBusy('') }
  }

  async function deleteUser(user: AdminUser) {
    if (!window.confirm(`Delete ${user.username}? This permanently removes the account and its Helix user data.`)) return
    setAccountBusy(user.id); setError(''); setStatus('')
    try {
      await accountRequest<{ ok: boolean }>(`/admin/users/${encodeURIComponent(user.id)}`, { method: 'DELETE' })
      setUsers((current) => current.filter((item) => item.id !== user.id))
      if (passwordUserId === user.id) { setPasswordUserId(''); setResetPassword('') }
      setStatus(`Deleted ${user.username}.`)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not delete user') }
    finally { setAccountBusy('') }
  }

  function field(key: string) {
    if (!(key in draft)) return null
    const def = DEFINITIONS[key]
    if (!def) return null
    const value = draft[key]
    return <div className="settings-control-row" key={key}>
      <div><strong>{def.label}</strong><span>{def.description}</span></div>
      {def.kind === 'boolean' ? <Toggle checked={Boolean(value)} onChange={(checked) => setDraft((current) => ({ ...current, [key]: checked }))} /> :
        <input type={def.kind === 'secret' ? 'password' : def.kind === 'number' ? 'number' : 'text'} value={String(value ?? '')} placeholder={def.kind === 'secret' ? 'Configured; leave blank to keep' : ''} onChange={(event) => setDraft((current) => ({ ...current, [key]: def.kind === 'number' ? Number(event.target.value) : event.target.value }))} />}
    </div>
  }

  const groupFields = (group: AdminSection) => Object.entries(DEFINITIONS).filter(([, def]) => def.group === group).map(([key]) => field(key))

  return <div className="settings-page settings-page-admin">
    <header className="settings-page-header"><div><span className="eyebrow">Administrator</span><h1>Admin Settings</h1><p>Global Helix configuration. Changes here affect every user.</p></div><div className="settings-page-actions"><button onClick={() => void load()}>Reload</button><button className="primary" disabled={!dirty || saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save changes'}</button></div></header>
    {error ? <div className="error-banner">{error}</div> : null}{status ? <div className="info-banner">{status}</div> : null}
    <div className="settings-layout">
      <nav className="settings-section-nav" aria-label="Admin settings sections">{SECTIONS.map(([key, label]) => <button type="button" className={section === key ? 'active' : ''} key={key} onClick={() => setSection(key)}>{label}</button>)}</nav>
      <section className="settings-section-content">
        {section === 'overview' ? <>
          <div className="settings-section-heading"><h2>Overview</h2><p>Server-wide status and configuration at a glance.</p></div>
          <div className="admin-status-grid"><div><span>Backend</span><strong className="status-ok">● Online</strong></div><div><span>Users</span><strong>{users.length}</strong></div><div><span>Configuration</span><strong>{Object.keys(settings).length} keys</strong></div></div>
          {qualityNotifications.length ? <div className="settings-card">
            <div className="settings-card-heading-row"><div><h3>Recent quality upgrades</h3><p>Successful slskd replacements recorded for administrators.</p></div></div>
            {qualityNotifications.slice(0, 5).map((notification) => <div className="settings-control-row" key={notification.id}>
              <div><strong>{notification.title}</strong><span style={{ whiteSpace: 'pre-line' }}>{notification.body}</span></div>
              <time className="muted">{new Date(notification.created_at.endsWith('Z') ? notification.created_at : `${notification.created_at}Z`).toLocaleString()}</time>
            </div>)}
          </div> : null}
          <details className="settings-diagnostics"><summary>View raw diagnostics</summary><pre>{JSON.stringify(health, null, 2)}</pre></details>
        </> : null}

        {section === 'users' ? <>
          <div className="settings-section-heading"><h2>Users</h2><p>Create accounts, manage access, reset passwords, or remove users.</p></div>
          <form className="admin-create-user" onSubmit={createUser}><input placeholder="Username" value={newUser.username} onChange={(e) => setNewUser((c) => ({ ...c, username: e.target.value }))} /><input type="password" placeholder="Temporary password" value={newUser.password} onChange={(e) => setNewUser((c) => ({ ...c, password: e.target.value }))} /><select value={newUser.role} onChange={(e) => setNewUser((c) => ({ ...c, role: e.target.value as 'user' | 'admin' }))}><option value="user">User</option><option value="admin">Administrator</option></select><button className="primary" disabled={newUser.username.trim().length < 3 || newUser.password.length < 8}>Create user</button></form>
          <div className="admin-settings-user-list admin-account-user-list">{users.map((user) => <div className="admin-account-user" key={user.id}>
            <span className="admin-account-user-identity"><strong>{user.username}</strong><small>{user.is_active ? 'Active' : 'Disabled'} · {user.can_import_subsonic ? 'Can add to Subsonic' : 'No Subsonic import access'}</small></span>
            <select value={user.role} disabled={accountBusy === user.id} onChange={(e) => void updateUser(user, { role: e.target.value as 'admin' | 'user' })}><option value="user">User</option><option value="admin">Administrator</option></select>
            {user.role === 'admin' ? <span className="admin-user-permission-fixed">Subsonic: Always allowed</span> : <label className="admin-user-permission-toggle"><span>Allow Subsonic import</span><Toggle checked={user.subsonic_import_override} onChange={(checked) => void updateUser(user, { subsonic_import_override: checked })} /></label>}
            <button disabled={accountBusy === user.id} onClick={() => void updateUser(user, { is_active: !user.is_active })}>{user.is_active ? 'Disable' : 'Enable'}</button>
            <div className="admin-account-actions">
              <button type="button" disabled={user.id === currentUserId || accountBusy === user.id} title={user.id === currentUserId ? 'Change your own password under personal Settings → Account.' : 'Reset password'} onClick={() => { setPasswordUserId((id) => id === user.id ? '' : user.id); setResetPassword('') }}>Reset password</button>
              <button type="button" className="danger" disabled={user.id === currentUserId || accountBusy === user.id} title={user.id === currentUserId ? 'You cannot delete the account you are signed in with.' : 'Delete user'} onClick={() => void deleteUser(user)}>Delete</button>
            </div>
            {passwordUserId === user.id ? <div className="admin-password-reset-row">
              <input type="password" autoComplete="new-password" placeholder="New password (8+ characters)" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} />
              <button type="button" className="primary" disabled={resetPassword.length < 8 || accountBusy === user.id} onClick={() => void submitPasswordReset(user)}>{accountBusy === user.id ? 'Resetting…' : 'Set password'}</button>
              <button type="button" disabled={accountBusy === user.id} onClick={() => { setPasswordUserId(''); setResetPassword('') }}>Cancel</button>
            </div> : null}
          </div>)}</div>
        </> : null}

        {(['library', 'playback', 'downloads', 'search'] as AdminSection[]).includes(section) ? <><div className="settings-section-heading"><h2>{SECTIONS.find(([key]) => key === section)?.[1]}</h2><p>{section === 'playback' ? 'Global resource limits and playback storage policy.' : section === 'downloads' ? 'Control how aggressively the server prepares media ahead of playback.' : section === 'library' ? 'Connection details for the shared music library.' : 'Server-wide catalog selection behavior.'}</p></div><div className="settings-card">{groupFields(section)}</div></> : null}
        {section === 'quality' ? <SlskdAdminSettingsSection /> : null}
        {section === 'advanced' ? <><div className="settings-section-heading"><h2>Advanced</h2><p>Low-level settings and legacy configuration. These are intentionally separated from normal administration.</p></div><div className="settings-card">{groupFields('advanced')}</div><div className="settings-obsolete-note"><strong>Review later</strong><p>The following existing settings appear unused or obsolete in the current backend and are intentionally not editable here yet:</p><code>{Array.from(OBSOLETE_CANDIDATES).join(', ')}</code></div></> : null}
      </section>
    </div>
  </div>
}
