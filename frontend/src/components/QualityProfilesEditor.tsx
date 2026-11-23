import { useCallback, useEffect, useRef, useState } from 'react'
import '../styles/quality-profiles.css'

type Tier = { label: string; codecs: string[]; min_bitrate: number; min_sample_rate: number; min_bit_depth: number }
type Policy = { tiers: Tier[]; target_tier: number; max_bitrate: number; max_sample_rate: number; max_bit_depth: number; allow_intermediate: boolean; stop_at_target: boolean; replace_lossless: boolean }
type Profile = { id: string; name: string; description: string; is_default: boolean; policy: Policy }
const CODECS = ['flac', 'alac', 'wav', 'aiff', 'aif', 'mp3', 'opus', 'aac', 'm4a', 'vorbis', 'ogg']
const LOSSLESS = new Set(['flac', 'alac', 'wav', 'aiff', 'aif'])
const blankTier = (): Tier => ({ label: 'New quality tier', codecs: ['flac'], min_bitrate: 0, min_sample_rate: 44100, min_bit_depth: 16 })

// Standard audio denominations. Keep saved nonstandard values visible until a user
// chooses a new one, so opening a profile never silently changes its policy.
const BITRATES_KBPS = [0, 64, 96, 128, 160, 192, 224, 256, 320, 384, 448, 512]
const SAMPLE_RATES_HZ = [0, 8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000]
const BIT_DEPTHS = [0, 8, 16, 24, 32]

function AudioDenomination({
  kind, value, onChange, maximum = false,
}: {
  kind: 'bitrate' | 'sample_rate' | 'bit_depth'
  value: number
  onChange: (value: number) => void
  maximum?: boolean
}) {
  const choices = kind === 'bitrate' ? BITRATES_KBPS.map(n => n * 1000) : kind === 'sample_rate' ? SAMPLE_RATES_HZ : BIT_DEPTHS
  const format = (n: number) => {
    if (n === 0) return maximum ? 'No maximum' : 'No minimum'
    if (kind === 'bitrate') return `${n / 1000} kbps`
    if (kind === 'sample_rate') return `${Number((n / 1000).toFixed(3))} kHz`
    return `${n}-bit`
  }
  return <select value={value} onChange={event => onChange(Number(event.target.value))}>
    {!choices.includes(value) && <option value={value}>{format(value)} (existing)</option>}
    {choices.map(choice => <option key={choice} value={choice}>{format(choice)}</option>)}
  </select>
}

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/quality-profiles${path}`, { credentials: 'include', ...init, headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) } })
  const text = await response.text()
  if (!response.ok) { let detail = text; try { detail = JSON.parse(text).detail ?? text } catch { /* plain text */ } throw new Error(detail || `Request failed (${response.status})`) }
  return text ? JSON.parse(text) as T : undefined as T
}
const body = (value: unknown) => JSON.stringify(value)

export function QualityProfilesEditor() {
  const [items, setItems] = useState<Profile[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Profile | null>(null)
  const [baseline, setBaseline] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const dragIndex = useRef<number | null>(null)
  const dirty = Boolean(draft && JSON.stringify({ name: draft.name, description: draft.description, policy: draft.policy }) !== baseline)

  const select = useCallback((profile: Profile) => {
    const fresh = clone(profile)
    setSelectedId(profile.id)
    setDraft(fresh)
    setBaseline(JSON.stringify({ name: fresh.name, description: fresh.description, policy: fresh.policy }))
    setError(''); setNotice('')
  }, [])
  const load = useCallback(async (preferred?: string) => {
    const result = await api<{ items: Profile[] }>('')
    setItems(result.items)
    const pick = result.items.find(row => row.id === preferred) ?? result.items.find(row => row.is_default) ?? result.items[0]
    if (pick) select(pick)
    else { setDraft(null); setSelectedId(null) }
  }, [select])
  useEffect(() => { void load().catch(e => setError(String(e))) }, [load])

  function updateDraft(fn: (current: Profile) => Profile) {
    setDraft(current => current ? fn(clone(current)) : current)
    setNotice('')
  }
  function updatePolicy(fn: (policy: Policy) => void) {
    updateDraft(next => { fn(next.policy); return next })
  }
  function updateTier(index: number, fn: (tier: Tier) => void) {
    updatePolicy(p => { fn(p.tiers[index]) })
  }
  function reorder(from: number, to: number) {
    if (from === to) return
    updatePolicy(p => {
      const [entry] = p.tiers.splice(from, 1)
      p.tiers.splice(to, 0, entry)
      if (p.target_tier === from) p.target_tier = to
      else if (from < p.target_tier && to >= p.target_tier) p.target_tier--
      else if (from > p.target_tier && to <= p.target_tier) p.target_tier++
    })
  }
  function validate(profile: Profile): string | null {
    const { policy } = profile
    if (!profile.name.trim() || profile.name.length > 100) return 'Profile name must contain 1–100 characters.'
    if (!policy.tiers.length || policy.tiers.length > 20) return 'A profile must contain 1–20 tiers.'
    if (policy.target_tier < 0 || policy.target_tier >= policy.tiers.length) return 'Choose a valid target tier.'
    for (const [index, tier] of policy.tiers.entries()) {
      if (!tier.codecs.length) return `Tier ${index + 1}: choose at least one codec.`
      if (tier.codecs.some(c => LOSSLESS.has(c)) && tier.codecs.some(c => !LOSSLESS.has(c))) return `Tier ${index + 1}: lossy and lossless formats must be separate.`
      for (const field of ['bitrate', 'sample_rate', 'bit_depth'] as const) {
        const ceiling = policy[`max_${field}`]
        if (ceiling && tier[`min_${field}`] > ceiling) return `Tier ${index + 1}: minimum ${field.replace('_', ' ')} exceeds maximum.`
      }
    }
    return null
  }
  async function act(task: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await task() } catch (e) { setError(e instanceof Error ? e.message : 'Operation failed') } finally { setBusy(false) }
  }
  function changeSelection(profile: Profile) {
    if (dirty && !window.confirm('Discard unsaved changes to the current quality profile?')) return
    select(profile)
  }
  const save = () => { if (!draft) return; const reason = validate(draft); if (reason) { setError(reason); return }
    void act(async () => { await api(`/${encodeURIComponent(draft.id)}`, { method: 'PATCH', body: body({ name: draft.name, description: draft.description, policy: draft.policy }) }); await load(draft.id); setNotice('Profile saved. New quality upgrade searches use this policy.') })
  }
  const add = () => { if (dirty && !window.confirm('Discard unsaved changes before adding a profile?')) return
    void act(async () => { const profile = await api<Profile>('', { method: 'POST', body: body({ name: 'New Profile', description: '', policy: { tiers: [blankTier()], target_tier: 0, max_bitrate: 0, max_sample_rate: 0, max_bit_depth: 0, allow_intermediate: false, stop_at_target: true, replace_lossless: false } }) }); await load(profile.id) })
  }
  const duplicate = () => { if (!draft) return; if (dirty && !window.confirm('Duplicate the last saved version? Unsaved changes will not be copied.')) return
    void act(async () => { const profile = await api<Profile>(`/${draft.id}/duplicate`, { method: 'POST' }); await load(profile.id) })
  }
  const makeDefault = () => { if (!draft) return; void act(async () => { await api(`/${draft.id}/default`, { method: 'POST' }); await load(draft.id); setNotice('Default profile updated.') }) }
  const remove = () => { if (!draft || draft.is_default || !window.confirm(`Delete profile “${draft.name}”? Assigned jobs will fall back to the default profile.`)) return
    void act(async () => { await api(`/${draft.id}`, { method: 'DELETE' }); await load() })
  }

  return <section className="qp-editor" aria-label="Quality profile editor">
    <div className="qp-editor-head"><div><h2>Quality Profiles</h2><p>Create ordered quality preferences for your Helix-managed music.</p></div><span className="qp-phase-note">Profiles apply to Helix-managed upgrade jobs</span></div>
    {error && <div className="qp-feedback qp-error" role="alert">{error}</div>}
    {notice && <div className="qp-feedback" role="status">{notice}</div>}
    <div className="qp-layout">
      <section className="qp-panel qp-profile-list" aria-label="Choose a quality profile">
        <div className="qp-panel-title"><h3>Quality Profiles</h3><button type="button" disabled={busy} title="Add profile" aria-label="Add profile" onClick={add}>＋ Add profile</button></div>
        <div className="qp-profile-strip">
          {items.map(profile => <button key={profile.id} type="button" className={`qp-profile ${profile.id === selectedId ? 'is-selected' : ''}`} aria-pressed={profile.id === selectedId} onClick={() => changeSelection(profile)}><span className="qp-profile-name"><strong>{profile.name}</strong>{profile.is_default && <span className="qp-profile-default">Default</span>}</span><span className="qp-profile-description">{profile.description || `${profile.policy.tiers.length} quality tiers`}</span></button>)}
        </div>
      </section>
      {draft ? <div className="qp-panel qp-detail">
        <div className="qp-detail-head"><div><label className="qp-label" htmlFor="qp-name">Profile name</label><input id="qp-name" value={draft.name} maxLength={100} onChange={e => updateDraft(p => ({ ...p, name: e.target.value }))}/>{draft.is_default && <span className="qp-default">Default</span>}</div><div className="qp-detail-actions"><button type="button" disabled={busy} onClick={duplicate}>Duplicate</button><button type="button" disabled={busy || draft.is_default} onClick={makeDefault}>Set default</button><button type="button" disabled={busy || draft.is_default} onClick={remove}>Delete</button><button type="button" disabled={busy || !dirty} className="qp-primary" onClick={save}>Save changes</button></div></div>
        <label className="qp-label" htmlFor="qp-description">Description</label><input id="qp-description" value={draft.description} maxLength={1000} onChange={e => updateDraft(p => ({ ...p, description: e.target.value }))}/>
        <div className="qp-edit-grid"><section className="qp-edit-section"><h3>Upgrade Progression</h3><p>Preferred quality levels, best to worst. Drag a row or use its arrow buttons.</p><div className="qp-tier-list">{draft.policy.tiers.map((tier, index) => <div key={`${selectedId}-${index}`} className={`qp-tier ${draft.policy.target_tier === index ? 'is-target' : ''}`} draggable onDragStart={e => { dragIndex.current = index; e.dataTransfer.effectAllowed = 'move' }} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (dragIndex.current !== null) reorder(dragIndex.current, index); dragIndex.current = null }} onDragEnd={() => { dragIndex.current = null }}>
          <span className="qp-grip" aria-hidden="true">⠿</span><div className="qp-tier-fields"><input aria-label={`Tier ${index + 1} name`} value={tier.label} maxLength={100} onChange={e => updateTier(index, t => { t.label = e.target.value })}/><div className="qp-codecs">{CODECS.map(codec => <label key={codec}><input type="checkbox" checked={tier.codecs.includes(codec)} onChange={e => updateTier(index, t => { t.codecs = e.target.checked ? [...t.codecs, codec] : t.codecs.filter(c => c !== codec) })}/>{codec.toUpperCase()}</label>)}</div><div className="qp-tier-numbers"><label>Min bitrate<AudioDenomination kind="bitrate" value={tier.min_bitrate} onChange={value => updateTier(index, t => { t.min_bitrate = value })}/></label><label>Min sample rate<AudioDenomination kind="sample_rate" value={tier.min_sample_rate} onChange={value => updateTier(index, t => { t.min_sample_rate = value })}/></label><label>Min bit depth<AudioDenomination kind="bit_depth" value={tier.min_bit_depth} onChange={value => updateTier(index, t => { t.min_bit_depth = value })}/></label></div></div><div className="qp-tier-buttons"><button type="button" title="Set target" aria-label={`Set ${tier.label} as target`} className={draft.policy.target_tier === index ? 'qp-target' : ''} onClick={() => updatePolicy(p => { p.target_tier = index })}>{draft.policy.target_tier === index ? 'Target' : 'Set target'}</button><button type="button" disabled={index === 0} onClick={() => reorder(index, index - 1)} aria-label={`Move ${tier.label} up`}>↑</button><button type="button" disabled={index === draft.policy.tiers.length - 1} onClick={() => reorder(index, index + 1)} aria-label={`Move ${tier.label} down`}>↓</button><button type="button" disabled={draft.policy.tiers.length === 1} onClick={() => updatePolicy(p => { p.tiers.splice(index, 1); if (p.target_tier === index) p.target_tier = 0; else if (p.target_tier > index) p.target_tier-- })} aria-label={`Remove ${tier.label}`}>×</button></div></div>)}</div><button type="button" disabled={draft.policy.tiers.length >= 20} onClick={() => updatePolicy(p => { p.tiers.push(blankTier()) })}>＋ Add quality level</button></section>
        <section className="qp-edit-section"><h3>Profile Settings</h3><p>Limits apply to every candidate, independently of tier order.</p><div className="qp-limits"><label>Maximum lossy bitrate<AudioDenomination kind="bitrate" maximum value={draft.policy.max_bitrate} onChange={value => updatePolicy(p => { p.max_bitrate = value })}/></label><label>Maximum sample rate<AudioDenomination kind="sample_rate" maximum value={draft.policy.max_sample_rate} onChange={value => updatePolicy(p => { p.max_sample_rate = value })}/></label><label>Maximum bit depth<AudioDenomination kind="bit_depth" maximum value={draft.policy.max_bit_depth} onChange={value => updatePolicy(p => { p.max_bit_depth = value })}/></label></div><p className="qp-hint">0 means no maximum. Lossless bitrate is not limited by the lossy bitrate setting.</p><div className="qp-options"><label><input type="checkbox" checked={draft.policy.allow_intermediate} onChange={e => updatePolicy(p => { p.allow_intermediate = e.target.checked })}/>Allow intermediate upgrades</label><label><input type="checkbox" checked={draft.policy.stop_at_target} onChange={e => updatePolicy(p => { p.stop_at_target = e.target.checked })}/>Stop once the target is reached</label></div></section></div>
      </div> : <div className="qp-panel">No profiles found.</div>}
    </div>
  </section>
}
