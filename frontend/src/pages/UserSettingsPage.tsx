import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { api } from '../api/client'
import type { UserSettings, UserSettingsPayload } from '../api/types'
import { TypographySettings } from '../components/TypographySettings'
import type { HelixFontId } from '../lib/fonts'
import '../styles/account-management.css'

type Settings = UserSettings & {
  appearance_font_single: boolean
  appearance_font_ui: HelixFontId
  appearance_font_display: HelixFontId
  appearance_font_secondary: HelixFontId
  appearance_font_lyrics: HelixFontId
  appearance_google_font_url_ui: string
  appearance_google_font_url_display: string
  appearance_google_font_url_secondary: string
  appearance_google_font_url_lyrics: string
}
type SettingsPayload = Omit<UserSettingsPayload, 'settings'> & { settings: Settings }

const SECTIONS = [
  ['account', 'Account'],
  ['appearance', 'Appearance'],
  ['playback', 'Playback'],
  ['search', 'Search & Discovery'],
  ['stations', 'Stations'],
  ['queue', 'Queue'],
  ['lobbies', 'Lobbies'],
  ['notifications', 'Notifications'],
  ['advanced', 'Advanced'],
] as const

type SectionKey = (typeof SECTIONS)[number][0]
type PlaybarStyle = 'helix' | 'ytmusic' | 'spotify' | 'pandora'
type SettingsWithPlaybar = Settings & { playback_bar_style?: PlaybarStyle }

function playbarStyle(settings: Settings): PlaybarStyle {
  const value = (settings as SettingsWithPlaybar).playback_bar_style
  return value === 'ytmusic' || value === 'spotify' || value === 'pandora' ? value : 'helix'
}

const ACCENT_PRESETS = [
  ['Amber', '#a95f18'],
  ['Orange', '#d46f16'],
  ['Red', '#c44b4b'],
  ['Purple', '#8656d9'],
  ['Blue', '#4979d1'],
  ['Green', '#2ca56f'],
] as const

const DEFAULT_PALETTE = {
  appearance_accent_color: '#a95f18',
  appearance_accent_contrast_color: '#fff8ef',
  appearance_logo_follow_accent: true,
  appearance_logo_color: '#d66f12',
  appearance_background_color: '#080a0d',
  appearance_surface_color: '#0d1014',
  appearance_surface_soft_color: '#12161b',
  appearance_surface_raised_color: '#171b20',
  appearance_sidebar_color: '#0a0d10',
  appearance_queue_color: '#0d1013',
  appearance_player_color: '#0b0d10',
  appearance_control_color: '#10141a',
  appearance_text_color: '#f5f2ec',
  appearance_muted_color: '#aaa9a5',
  appearance_faint_color: '#747570',
  appearance_border_color: '#252a31',
  appearance_danger_color: '#ff647d',
  appearance_success_color: '#35e09b',
} as const

const PALETTE_FIELDS = [
  ['appearance_accent_color', 'Primary / accent', 'Active states, primary controls, progress, and highlights.'],
  ['appearance_accent_contrast_color', 'Text on primary', 'Text and icons displayed on solid primary-colored controls.'],
  ['appearance_background_color', 'App background', 'The deepest application canvas behind pages and panels.'],
  ['appearance_surface_color', 'Surface', 'Primary panel and card surface color.'],
  ['appearance_surface_soft_color', 'Soft surface', 'Subtle rows, controls, and secondary containers.'],
  ['appearance_surface_raised_color', 'Raised surface', 'Menus, elevated controls, and stronger layered surfaces.'],
  ['appearance_sidebar_color', 'Sidebar', 'The navigation rail and account area on the left side of Helix.'],
  ['appearance_queue_color', 'Queue panel', 'Background of the global Up Next panel.'],
  ['appearance_player_color', 'Playback bar', 'Background of the persistent playback controls along the bottom.'],
  ['appearance_control_color', 'Inputs & controls', 'Text fields, selects, secondary buttons, and inactive segmented controls.'],
  ['appearance_text_color', 'Primary text', 'Main titles, labels, and high-emphasis text.'],
  ['appearance_muted_color', 'Secondary text', 'Descriptions, metadata, and supporting text.'],
  ['appearance_faint_color', 'Faint text', 'Low-emphasis timestamps, hints, and tertiary information.'],
  ['appearance_border_color', 'Borders', 'Main outlines and structural dividers.'],
  ['appearance_danger_color', 'Danger', 'Destructive actions, errors, and warning emphasis.'],
  ['appearance_success_color', 'Success', 'Healthy, connected, available, and completed states.'],
] as const

const DEFAULT_SETTINGS: Settings = {
  ...DEFAULT_PALETTE,
  appearance_reduce_motion: false,
  appearance_artwork_backgrounds: true,
  appearance_ui_density: 'comfortable',
  appearance_artwork_radius: 'soft',
  appearance_font_single: true,
  appearance_font_ui: 'jetbrains-mono',
  appearance_font_display: 'jetbrains-mono',
  appearance_font_secondary: 'jetbrains-mono',
  appearance_font_lyrics: 'jetbrains-mono',
  appearance_google_font_url_ui: '',
  appearance_google_font_url_display: '',
  appearance_google_font_url_secondary: '',
  appearance_google_font_url_lyrics: '',
  queue_add_position: 'append',
  queue_show_duration: true,
  queue_show_playing_indicator: true,
  playback_default_volume: 0.85,
  search_default_mode: 'hybrid',
  search_default_tab: 'songs',
  station_queue_ahead: 3,
  lobbies_default_name: 'Shared Lobby',
  lobbies_default_guests_can_add: false,
  lobbies_auto_copy_invite: false,
  notifications_import_queued: true,
  notifications_duration: 'normal',
  advanced_custom_css: '',
}

const DEFAULT_PAYLOAD: SettingsPayload = {
  settings: DEFAULT_SETTINGS,
  limits: { station_queue_ahead_max: 10 },
}

function SettingToggle({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return <button type="button" className={`settings-toggle ${checked ? 'on' : ''}`} role="switch" aria-checked={checked} onClick={() => onChange(!checked)}><span className="settings-toggle-thumb" /></button>
}

function Segmented<T extends string>({ value, options, onChange }: { value: T; options: Array<[T, string]>; onChange: (value: T) => void }) {
  return <div className="settings-segmented">{options.map(([key, label]) => <button type="button" key={key} className={value === key ? 'active' : ''} onClick={() => onChange(key)}>{label}</button>)}</div>
}

function ColorField({ label, description, value, onChange }: { label: string; description: string; value: string; onChange: (value: string) => void }) {
  return <label className="settings-palette-field">
    <span className="settings-palette-copy"><strong>{label}</strong><span>{description}</span></span>
    <span className="settings-palette-control"><input type="color" value={value} onChange={(event) => onChange(event.target.value)} aria-label={`${label} color`} /><code>{value}</code></span>
  </label>
}

async function accountRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
  })
  const responseText = await response.text()
  if (!response.ok) {
    let detail = responseText || `${response.status} ${response.statusText}`
    try { detail = JSON.parse(responseText).detail || detail } catch { /* use response text */ }
    throw new Error(detail)
  }
  return responseText ? JSON.parse(responseText) as T : undefined as T
}

export function UserSettingsPage() {
  const [section, setSection] = useState<SectionKey>('appearance')
  const [payload, setPayload] = useState<SettingsPayload>(DEFAULT_PAYLOAD)
  const [draft, setDraft] = useState<Settings>(DEFAULT_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [passwordSaving, setPasswordSaving] = useState(false)
  const [passwordDraft, setPasswordDraft] = useState({ current: '', next: '', confirm: '' })
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const savedPayloadRef = useRef(payload)

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(payload.settings), [draft, payload.settings])
  const safeUi = new URLSearchParams(window.location.search).get('safe-ui') === '1'

  useEffect(() => { savedPayloadRef.current = payload }, [payload])

  async function load() {
    setLoading(true)
    try {
      const next = await api.userSettings() as SettingsPayload
      setPayload(next)
      setDraft({ ...DEFAULT_SETTINGS, ...next.settings })
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load your settings')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    return () => {
      window.dispatchEvent(new CustomEvent('helix-user-settings-updated', { detail: savedPayloadRef.current }))
    }
  }, [])

  useEffect(() => {
    if (loading) return
    const liveSettings = {
      ...draft,
      advanced_custom_css: previewing ? draft.advanced_custom_css : payload.settings.advanced_custom_css,
    }
    window.dispatchEvent(new CustomEvent('helix-user-settings-updated', {
      detail: { settings: liveSettings, limits: payload.limits },
    }))
  }, [draft, payload.limits, payload.settings.advanced_custom_css, previewing, loading])

  function update(key: keyof Settings, value: unknown) {
    setDraft((current) => ({ ...current, [key]: value } as Settings))
  }

  function stopPreview() {
    setPreviewing(false)
    window.dispatchEvent(new CustomEvent('helix-user-settings-updated', { detail: payload }))
  }

  async function save() {
    if (!dirty || saving) return
    setSaving(true)
    setError('')
    setStatus('')
    try {
      const patch: Record<string, unknown> = {}
      for (const key of Object.keys(draft) as Array<keyof Settings>) {
        if (draft[key] !== payload.settings[key]) patch[String(key)] = draft[key]
      }
      const next = await api.updateUserSettings(patch as Partial<UserSettings>) as SettingsPayload
      setPayload(next)
      setDraft({ ...DEFAULT_SETTINGS, ...next.settings })
      setPreviewing(false)
      window.dispatchEvent(new CustomEvent('helix-user-settings-updated', { detail: next }))
      setStatus('Your settings were saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save your settings')
    } finally {
      setSaving(false)
    }
  }

  async function changePassword() {
    if (passwordSaving) return
    if (passwordDraft.next.length < 8) { setError('New password must be at least 8 characters.'); return }
    if (passwordDraft.next !== passwordDraft.confirm) { setError('New passwords do not match.'); return }
    setPasswordSaving(true); setError(''); setStatus('')
    try {
      await accountRequest<{ ok: boolean }>('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: passwordDraft.current, new_password: passwordDraft.next }),
      })
      setPasswordDraft({ current: '', next: '', confirm: '' })
      setStatus('Password changed. Other signed-in sessions were logged out.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change password')
    } finally {
      setPasswordSaving(false)
    }
  }

  async function resetAll() {
    if (!window.confirm('Reset all of your Helix preferences, including custom CSS?')) return
    setSaving(true)
    setError('')
    try {
      const next = await api.resetUserSettings() as SettingsPayload
      setPayload(next)
      setDraft({ ...DEFAULT_SETTINGS, ...next.settings })
      setPreviewing(false)
      window.dispatchEvent(new CustomEvent('helix-user-settings-updated', { detail: next }))
      setStatus('Your settings were reset to Helix defaults.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reset your settings')
    } finally {
      setSaving(false)
    }
  }

  function exportSettings() {
    const blob = new Blob([JSON.stringify({ version: 1, settings: draft }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'helix-user-settings.json'
    link.click()
    URL.revokeObjectURL(url)
  }

  function importSettings() {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'application/json,.json'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        const parsed = JSON.parse(await file.text()) as { settings?: Record<string, unknown> } | Record<string, unknown>
        const incoming = 'settings' in parsed && parsed.settings && typeof parsed.settings === 'object' ? parsed.settings : parsed
        const next = { ...draft } as Record<string, unknown>
        for (const key of Object.keys(DEFAULT_SETTINGS)) {
          if (key in incoming) next[key] = (incoming as Record<string, unknown>)[key]
        }
        setDraft(next as Settings)
        setStatus('Imported settings are ready to preview or save.')
        setError('')
      } catch {
        setError('That file is not a valid Helix settings export.')
      }
    }
    input.click()
  }

  const maxAhead = payload.limits.station_queue_ahead_max

  return (
    <div className="settings-page settings-page-user">
      <header className="settings-page-header">
        <div><span className="eyebrow">Personal</span><h1>Settings</h1><p>Customize how Helix looks and behaves for your account.</p></div>
        <div className="settings-page-actions">
          <button type="button" disabled={!dirty || saving} onClick={() => { setDraft(payload.settings); stopPreview(); setStatus('') }}>Discard</button>
          <button className="primary" type="button" disabled={!dirty || saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save changes'}</button>
        </div>
      </header>

      {safeUi ? <div className="info-banner">Safe UI mode is active. Your saved custom CSS is temporarily disabled.</div> : null}
      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}

      <div className="settings-layout">
        <nav className="settings-section-nav" aria-label="Settings sections">
          {SECTIONS.map(([key, label]) => <button type="button" className={section === key ? 'active' : ''} key={key} onClick={() => setSection(key)}>{label}</button>)}
        </nav>

        <section className="settings-section-content" aria-busy={loading}>
          {section === 'account' ? <>
            <div className="settings-section-heading"><h2>Account</h2><p>Manage your Helix account credentials.</p></div>
            <div className="settings-card account-password-card">
              <div className="settings-card-heading-row"><div><h3>Change password</h3><p>Enter your current password, then choose a new password with at least 8 characters.</p></div></div>
              <label className="settings-control-row"><div><strong>Current password</strong><span>Required to confirm this account change.</span></div><input type="password" autoComplete="current-password" value={passwordDraft.current} onChange={(event) => setPasswordDraft((current) => ({ ...current, current: event.target.value }))} /></label>
              <label className="settings-control-row"><div><strong>New password</strong><span>Use at least 8 characters.</span></div><input type="password" autoComplete="new-password" value={passwordDraft.next} onChange={(event) => setPasswordDraft((current) => ({ ...current, next: event.target.value }))} /></label>
              <label className="settings-control-row"><div><strong>Confirm new password</strong><span>Enter the new password again.</span></div><input type="password" autoComplete="new-password" value={passwordDraft.confirm} onChange={(event) => setPasswordDraft((current) => ({ ...current, confirm: event.target.value }))} /></label>
              <div className="account-password-actions"><button type="button" className="primary" disabled={passwordSaving || !passwordDraft.current || passwordDraft.next.length < 8 || passwordDraft.next !== passwordDraft.confirm} onClick={() => void changePassword()}>{passwordSaving ? 'Changing…' : 'Change password'}</button></div>
            </div>
          </> : null}

          {section === 'appearance' ? <>
            <div className="settings-section-heading"><h2>Appearance</h2><p>Personalize Helix without changing the experience for anyone else.</p></div>

            <div className="settings-card">
              <div className="settings-control-row">
                <div><strong>Primary color presets</strong><span>Quickly choose the main interaction color, or customize the entire palette below.</span></div>
                <div className="settings-accent-picker">
                  <div className="settings-color-presets">{ACCENT_PRESETS.map(([label, color]) => <button type="button" key={color} className={draft.appearance_accent_color.toLowerCase() === color ? 'active' : ''} style={{ '--swatch-color': color } as CSSProperties} aria-label={label} title={label} onClick={() => update('appearance_accent_color', color)}><span /></button>)}</div>
                  <div className="settings-color-control"><input aria-label="Custom primary color" type="color" value={draft.appearance_accent_color} onChange={(event) => update('appearance_accent_color', event.target.value)} /><code>{draft.appearance_accent_color}</code></div>
                </div>
              </div>
            </div>

            <div className="settings-card">
              <div className="settings-control-row">
                <div><strong>Helix logo color</strong><span>Let the sidebar logo follow the primary color, or give it its own color.</span></div>
                <div className="settings-logo-color-controls">
                  <label className="settings-inline-toggle"><input type="checkbox" checked={draft.appearance_logo_follow_accent} onChange={(event) => update('appearance_logo_follow_accent', event.target.checked)} /><span>Follow primary</span></label>
                  <div className="settings-color-control"><input aria-label="Custom Helix logo color" type="color" value={draft.appearance_logo_color} disabled={draft.appearance_logo_follow_accent} onChange={(event) => update('appearance_logo_color', event.target.value)} /><code>{draft.appearance_logo_follow_accent ? draft.appearance_accent_color : draft.appearance_logo_color}</code></div>
                </div>
              </div>
            </div>

            <div className="settings-card settings-palette-card">
              <div className="settings-card-heading-row">
                <div><h3>Interface palette</h3><p>These map to Helix's core design tokens, so you can recolor the application instead of being limited to one accent.</p></div>
                <button type="button" className="settings-reset-palette" onClick={() => setDraft((current) => ({ ...current, ...DEFAULT_PALETTE }))}>Restore Helix palette</button>
              </div>
              <div className="settings-palette-grid">
                {PALETTE_FIELDS.map(([key, label, description]) => <ColorField key={key} label={label} description={description} value={String(draft[key])} onChange={(value) => update(key, value)} />)}
              </div>
              <p className="settings-note">The supported palette covers Helix's shared theme colors. Component-specific or experimental colors can still be overridden under Advanced → Custom CSS.</p>
            </div>

            <TypographySettings settings={draft} onChange={update} />

            <div className="settings-card">
              <div className="settings-control-row"><div><strong>UI density</strong><span>Adjust spacing throughout navigation, lists, cards, and controls.</span></div><Segmented value={draft.appearance_ui_density} options={[["compact", "Compact"], ["comfortable", "Comfortable"], ["spacious", "Spacious"]]} onChange={(value) => update('appearance_ui_density', value)} /></div>
              <div className="settings-control-row"><div><strong>Artwork corners</strong><span>Choose how album and track artwork is shaped throughout common Helix views.</span></div><Segmented value={draft.appearance_artwork_radius} options={[["square", "Square"], ["soft", "Soft"], ["rounded", "Rounded"]]} onChange={(value) => update('appearance_artwork_radius', value)} /></div>
              <div className="settings-control-row"><div><strong>Artwork backgrounds</strong><span>Allow album artwork to softly influence hero backgrounds where supported.</span></div><SettingToggle checked={draft.appearance_artwork_backgrounds} onChange={(value) => update('appearance_artwork_backgrounds', value)} /></div>
              <div className="settings-control-row"><div><strong>Reduce motion</strong><span>Minimize decorative animation such as queue equalizers and transitions.</span></div><SettingToggle checked={draft.appearance_reduce_motion} onChange={(value) => update('appearance_reduce_motion', value)} /></div>
            </div>
          </> : null}

          {section === 'playback' ? <>
            <div className="settings-section-heading"><h2>Playback</h2><p>Defaults that apply when you use Helix.</p></div>
            <div className="settings-card">
              <div className="settings-control-row settings-control-row-stack-mobile">
                <div><strong>Playbar style</strong><span>Change the layout and control placement while keeping Helix playback behavior the same.</span></div>
                <Segmented value={playbarStyle(draft)} options={[["helix", "Helix"], ["ytmusic", "YTMusic"], ["spotify", "Spotify"], ["pandora", "Pandora"]]} onChange={(value) => update('playback_bar_style' as keyof Settings, value)} />
              </div>
              <label className="settings-control-row"><div><strong>Default volume</strong><span>Used as your initial playback volume on a new browser or device.</span></div><div className="settings-range-control"><input type="range" min="0" max="100" value={Math.round(draft.playback_default_volume * 100)} onChange={(event) => update('playback_default_volume', Number(event.target.value) / 100)} /><output>{Math.round(draft.playback_default_volume * 100)}%</output></div></label>
            </div>
          </> : null}

          {section === 'search' ? <>
            <div className="settings-section-heading"><h2>Search & Discovery</h2><p>Choose the defaults Helix uses when you open Search.</p></div>
            <div className="settings-card">
              <label className="settings-control-row"><div><strong>Default search source</strong><span>You can still change this at any time from the Search page.</span></div><select value={draft.search_default_mode} onChange={(event) => update('search_default_mode', event.target.value)}><option value="hybrid">All</option><option value="subsonic">Library</option><option value="ytmusic">YTMusic</option></select></label>
              <label className="settings-control-row"><div><strong>Default results section</strong><span>The tab shown first after a search.</span></div><select value={draft.search_default_tab} onChange={(event) => update('search_default_tab', event.target.value)}><option value="songs">Songs</option><option value="albums">Albums</option><option value="artists">Artists</option></select></label>
            </div>
          </> : null}

          {section === 'stations' ? <>
            <div className="settings-section-heading"><h2>Stations</h2><p>Control how much upcoming station playback Helix keeps visible for you.</p></div>
            <div className="settings-card">
              <label className="settings-control-row settings-control-row-stack-mobile"><div><strong>Tracks to keep ahead</strong><span>Helix will try to keep this many upcoming station-generated tracks in your queue. Your administrator allows up to {maxAhead}.</span></div><div className="settings-number-stepper"><button type="button" onClick={() => update('station_queue_ahead', Math.max(1, draft.station_queue_ahead - 1))}>−</button><output>{draft.station_queue_ahead}</output><button type="button" onClick={() => update('station_queue_ahead', Math.min(maxAhead, draft.station_queue_ahead + 1))}>+</button></div></label>
              <p className="settings-note">This controls the logical station queue. The server administrator separately controls how far Helix actually downloads/prefetches media.</p>
            </div>
          </> : null}

          {section === 'queue' ? <>
            <div className="settings-section-heading"><h2>Queue</h2><p>Choose how explicit Add to queue actions behave and how your personal Up Next panel is displayed.</p></div>
            <div className="settings-card">
              <div className="settings-control-row settings-control-row-stack-mobile"><div><strong>Add to queue placement</strong><span>Choose whether Add to queue puts new music at the end or directly after the currently playing track.</span></div><Segmented value={draft.queue_add_position} options={[['append', 'End of queue'], ['next', 'Play next']]} onChange={(value) => update('queue_add_position', value)} /></div>
              <p className="settings-note">This only changes explicit Add to queue actions. Playing a song, album, or playlist still replaces the current queue as normal.</p>
              <div className="settings-control-row"><div><strong>Show track durations</strong><span>Display each queued track's duration on the right side of the queue.</span></div><SettingToggle checked={draft.queue_show_duration} onChange={(value) => update('queue_show_duration', value)} /></div>
              <div className="settings-control-row"><div><strong>Animated now-playing indicator</strong><span>Show the moving equalizer bars beside the active queue item.</span></div><SettingToggle checked={draft.queue_show_playing_indicator} onChange={(value) => update('queue_show_playing_indicator', value)} /></div>
            </div>
          </> : null}

          {section === 'lobbies' ? <>
            <div className="settings-section-heading"><h2>Lobbies</h2><p>Defaults used when you create a new shared listening room.</p></div>
            <div className="settings-card">
              <label className="settings-control-row"><div><strong>Default lobby name</strong><span>Pre-fills the lobby name field. You can still change it before creating the room.</span></div><input type="text" maxLength={80} value={draft.lobbies_default_name} onChange={(event) => update('lobbies_default_name', event.target.value)} /></label>
              <div className="settings-control-row"><div><strong>Guests can add to queue</strong><span>Use this as the default permission when creating a lobby.</span></div><SettingToggle checked={draft.lobbies_default_guests_can_add} onChange={(value) => update('lobbies_default_guests_can_add', value)} /></div>
              <div className="settings-control-row"><div><strong>Copy invite after creation</strong><span>Automatically copy the invite URL when a new lobby is successfully created.</span></div><SettingToggle checked={draft.lobbies_auto_copy_invite} onChange={(value) => update('lobbies_auto_copy_invite', value)} /></div>
            </div>
          </> : null}

          {section === 'notifications' ? <>
            <div className="settings-section-heading"><h2>Notifications</h2><p>Choose which lightweight interface notifications Helix shows you.</p></div>
            <div className="settings-card">
              <div className="settings-control-row"><div><strong>Import queued</strong><span>Show the floating confirmation whenever you send a track or album to Subsonic.</span></div><SettingToggle checked={draft.notifications_import_queued} onChange={(value) => update('notifications_import_queued', value)} /></div>
              <label className="settings-control-row"><div><strong>Notification duration</strong><span>How long lightweight confirmations stay visible.</span></div><select value={draft.notifications_duration} onChange={(event) => update('notifications_duration', event.target.value)}><option value="short">Short</option><option value="normal">Normal</option><option value="long">Long</option></select></label>
            </div>
          </> : null}

          {section === 'advanced' ? <>
            <div className="settings-section-heading"><h2>Advanced</h2><p>Power-user customization and portability. These changes affect only your account.</p></div>
            <div className="settings-card settings-css-card">
              <label><strong>Custom CSS</strong><span>Loaded after Helix's normal styles. You can override any selector or design token.</span></label>
              <textarea spellCheck={false} value={draft.advanced_custom_css} onChange={(event) => update('advanced_custom_css', event.target.value)} placeholder={`/* Example */\n:root {\n  --accent: #3b82f6;\n}`} />
              <div className="settings-css-toolbar"><div><button type="button" className={previewing ? 'active' : ''} onClick={() => previewing ? stopPreview() : setPreviewing(true)}>{previewing ? 'Stop preview' : 'Preview unsaved CSS'}</button><button type="button" onClick={() => update('advanced_custom_css', '')}>Clear CSS</button></div><span>{draft.advanced_custom_css.length.toLocaleString()} / 100,000</span></div>
              <p className="settings-note">Recovery: open Helix with <code>?safe-ui=1</code> to temporarily disable your custom CSS if an override makes the interface unusable.</p>
            </div>
            <div className="settings-card settings-advanced-actions">
              <div className="settings-control-row"><div><strong>Export preferences</strong><span>Download your current draft as JSON so you can back it up or move it to another account.</span></div><button type="button" onClick={exportSettings}>Export</button></div>
              <div className="settings-control-row"><div><strong>Import preferences</strong><span>Load a Helix settings JSON file into this draft. Nothing changes on the server until you save.</span></div><button type="button" onClick={importSettings}>Import</button></div>
              <div className="settings-control-row settings-danger-row"><div><strong>Reset all preferences</strong><span>Return your account to Helix defaults and remove your custom CSS.</span></div><button type="button" className="danger" disabled={saving} onClick={() => void resetAll()}>Reset to defaults</button></div>
            </div>
          </> : null}
        </section>
      </div>
    </div>
  )
}
