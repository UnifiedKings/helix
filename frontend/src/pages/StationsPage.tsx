import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { Capabilities, Station, StationConfigOption, StationProviderInfo } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>
type StationConfig = Record<string, unknown>

function IconPlay() {
  return <span aria-hidden="true">▶</span>
}

function StationStat({ icon, value, label }: { icon: string; value: string | number; label: string }) {
  return (
    <div className="station-stat-card">
      <span className="station-stat-icon" aria-hidden="true">{icon}</span>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  )
}

function optionDefault(option: StationConfigOption): unknown {
  if (option.default !== undefined && option.default !== null) return option.default
  if (option.type === 'boolean') return false
  if (option.type === 'number' || option.type === 'integer') return option.min ?? 0
  if (option.type === 'multiselect') return []
  return ''
}

function configFromProvider(provider: StationProviderInfo, existing?: StationConfig): StationConfig {
  const next: StationConfig = {}
  for (const option of provider.config_options ?? []) {
    next[option.key] = existing && existing[option.key] !== undefined ? existing[option.key] : optionDefault(option)
  }
  return next
}

function providerForCapabilities(provider: StationProviderInfo, capabilities: Capabilities | null): StationProviderInfo {
  if (capabilities?.subsonic_configured !== false) return provider
  return {
    ...provider,
    config_options: (provider.config_options ?? []).map((option) => {
      if (option.key !== 'source_mode') return option
      return {
        ...option,
        default: 'prefer_library',
        choices: (option.choices ?? []).filter((choice) => String(choice.value) !== 'library_only'),
        description: 'Subsonic is not configured, so this station can use discovery tracks only.',
      }
    }),
  }
}

function withFreshCoverUrl(station: Station): Station {
  const baseUrl = station.cover_url || station.thumbnail_url || `/api/stations/${encodeURIComponent(station.id)}/cover`
  const separator = baseUrl.includes('?') ? '&' : '?'
  const freshUrl = `${baseUrl}${separator}clientCoverBust=${Date.now()}`
  return {
    ...station,
    cover_url: freshUrl,
    thumbnail_url: freshUrl,
    has_custom_cover: true,
  }
}

function providerLabel(provider?: StationProviderInfo, fallback?: string) {
  return provider?.display_name || fallback || 'Unknown station type'
}

function configSummary(station: Station, provider?: StationProviderInfo) {
  const config = station.config ?? {}
  const seed = String(config.seed_artist || station.seed_artist || config.seed_title || station.seed_title || '').trim()
  if (seed) return seed
  const firstRequired = provider?.config_options?.find((option) => option.required)
  if (firstRequired && config[firstRequired.key]) return String(config[firstRequired.key])
  return 'Configured station'
}

function coerceConfigValue(option: StationConfigOption, raw: string | boolean | string[]): unknown {
  if (option.type === 'boolean') return Boolean(raw)
  if (option.type === 'integer') {
    const parsed = Number.parseInt(String(raw), 10)
    return Number.isFinite(parsed) ? parsed : optionDefault(option)
  }
  if (option.type === 'number') {
    const parsed = Number.parseFloat(String(raw))
    return Number.isFinite(parsed) ? parsed : optionDefault(option)
  }
  if (option.type === 'multiselect') return Array.isArray(raw) ? raw : []
  return String(raw)
}

function ConfigOptionField({ option, value, onChange }: { option: StationConfigOption; value: unknown; onChange: (value: unknown) => void }) {
  const id = `station-config-${option.key}`
  const commonProps = {
    id,
    name: option.key,
  }

  let control
  if (option.type === 'boolean') {
    control = (
      <label className="station-checkbox-field">
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        <span>{Boolean(value) ? 'Enabled' : 'Disabled'}</span>
      </label>
    )
  } else if (option.type === 'select') {
    control = (
      <select {...commonProps} value={String(value ?? '')} onChange={(event) => onChange(event.target.value)}>
        {(option.choices ?? []).map((choice) => (
          <option key={String(choice.value)} value={String(choice.value)}>{choice.label ?? String(choice.value)}</option>
        ))}
      </select>
    )
  } else if (option.type === 'multiselect') {
    const selected = Array.isArray(value) ? value.map(String) : []
    control = (
      <select
        {...commonProps}
        multiple
        value={selected}
        onChange={(event) => onChange(Array.from(event.target.selectedOptions).map((item) => item.value))}
      >
        {(option.choices ?? []).map((choice) => (
          <option key={String(choice.value)} value={String(choice.value)}>{choice.label ?? String(choice.value)}</option>
        ))}
      </select>
    )
  } else if (option.type === 'textarea') {
    control = <textarea {...commonProps} value={String(value ?? '')} onChange={(event) => onChange(event.target.value)} rows={4} />
  } else if (option.type === 'number' || option.type === 'integer') {
    control = (
      <input
        {...commonProps}
        type="number"
        min={option.min}
        max={option.max}
        step={option.step ?? (option.type === 'integer' ? 1 : 0.05)}
        value={String(value ?? '')}
        onChange={(event) => onChange(coerceConfigValue(option, event.target.value))}
      />
    )
  } else {
    control = <input {...commonProps} value={String(value ?? '')} onChange={(event) => onChange(event.target.value)} />
  }

  return (
    <label className="station-config-field" htmlFor={id}>
      <span className="station-config-label">
        {option.label}
        {option.required ? <strong>Required</strong> : null}
      </span>
      {option.description ? <small>{option.description}</small> : null}
      {control}
    </label>
  )
}

function StationConfigForm({ provider, config, onChange }: { provider: StationProviderInfo; config: StationConfig; onChange: (config: StationConfig) => void }) {
  if (!provider.config_options?.length) {
    return <div className="info-banner">This station type does not expose configurable options.</div>
  }

  return (
    <div className="station-config-grid">
      {provider.config_options.map((option) => (
        <ConfigOptionField
          key={option.key}
          option={option}
          value={config[option.key] ?? optionDefault(option)}
          onChange={(value) => onChange({ ...config, [option.key]: value })}
        />
      ))}
    </div>
  )
}

export function StationsPage() {
  const player = useOutletContext<PlayerContext>()
  const [providers, setProviders] = useState<StationProviderInfo[]>([])
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [stations, setStations] = useState<Station[]>([])
  const [selectedType, setSelectedType] = useState('')
  const [stationName, setStationName] = useState('')
  const [config, setConfig] = useState<StationConfig>({})
  const [editingStation, setEditingStation] = useState<Station | null>(null)
  const [editingName, setEditingName] = useState('')
  const [editingType, setEditingType] = useState('')
  const [editingConfig, setEditingConfig] = useState<StationConfig>({})
  const [sortMode, setSortMode] = useState('recent')
  const [busy, setBusy] = useState(false)
  const [coverBusy, setCoverBusy] = useState(false)
  const [selectedCoverFile, setSelectedCoverFile] = useState<File | null>(null)
  const [selectedCoverPreviewUrl, setSelectedCoverPreviewUrl] = useState('')
  const [isCreateModalOpen, setCreateModalOpen] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [startingStation, setStartingStation] = useState<Station | null>(null)

  const visibleProviders = useMemo(() => providers.map((provider) => providerForCapabilities(provider, capabilities)), [providers, capabilities])
  const providerByType = useMemo(() => new Map(visibleProviders.map((provider) => [provider.station_type, provider])), [visibleProviders])
  const selectedProvider = selectedType ? providerByType.get(selectedType) : undefined
  const editingProvider = providerByType.get(editingType) ?? visibleProviders[0]

  const sortedStations = useMemo(() => {
    const rows = [...stations]
    if (sortMode === 'name') rows.sort((a, b) => a.name.localeCompare(b.name))
    else if (sortMode === 'created') rows.sort((a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? '')))
    else rows.sort((a, b) => String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? '')))
    return rows
  }, [stations, sortMode])

  async function load() {
    try {
      setError('')
      const [typeRows, stationRows, capabilityRows] = await Promise.all([api.stationTypes(), api.stations(), api.capabilities()])
      setProviders(typeRows)
      setStations(stationRows)
      setCapabilities(capabilityRows)
      if (selectedType) {
        const currentProvider = typeRows.find((provider) => provider.station_type === selectedType)
        if (currentProvider) {
          setConfig((current) => Object.keys(current).length ? current : configFromProvider(currentProvider))
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load stations')
    }
  }

  useEffect(() => { void load() }, [])

  useEffect(() => {
    return () => {
      if (selectedCoverPreviewUrl) URL.revokeObjectURL(selectedCoverPreviewUrl)
    }
  }, [selectedCoverPreviewUrl])

  function clearPendingCover() {
    setSelectedCoverFile(null)
    setSelectedCoverPreviewUrl('')
  }

  function choosePendingCover(file: File | undefined) {
    if (!file) return
    setSelectedCoverFile(file)
    setSelectedCoverPreviewUrl(URL.createObjectURL(file))
    setStatus(`Selected cover: ${file.name}`)
    setError('')
  }

  function openCreateModal() {
    const fallbackType = selectedType || visibleProviders[0]?.station_type || ''
    const provider = fallbackType ? providerByType.get(fallbackType) : undefined
    setSelectedType(fallbackType)
    if (provider) setConfig((current) => Object.keys(current).length ? current : configFromProvider(provider))
    setCreateModalOpen(true)
    setStatus('')
    setError('')
  }

  function closeCreateModal() {
    if (busy) return
    setCreateModalOpen(false)
  }

  function closeEditor() {
    setEditingStation(null)
    clearPendingCover()
  }

  function chooseProvider(stationType: string) {
    const provider = providerByType.get(stationType)
    setSelectedType(stationType)
    if (provider) setConfig(configFromProvider(provider, config))
  }

  function chooseEditingProvider(stationType: string) {
    const provider = providerByType.get(stationType)
    setEditingType(stationType)
    if (provider) setEditingConfig(configFromProvider(provider, editingConfig))
  }

  function startEditing(station: Station) {
    const stationType = station.station_type || visibleProviders[0]?.station_type || ''
    const provider = providerByType.get(stationType)
    setEditingStation(station)
    setEditingName(station.name)
    setEditingType(stationType)
    setEditingConfig(provider ? configFromProvider(provider, station.config) : { ...(station.config ?? {}) })
    clearPendingCover()
    setStatus('')
    setError('')
  }

  async function createStation(event: FormEvent) {
    event.preventDefault()
    if (!selectedProvider) return
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const name = stationName.trim() || `${selectedProvider.display_name}`
      await api.createStation({
        name,
        station_type: selectedProvider.station_type,
        config,
        seed_type: String(config.seed_type || 'artist'),
        seed_artist: String(config.seed_artist || ''),
        seed_title: String(config.seed_title || ''),
      })
      setStationName('')
      setSelectedType('')
      setConfig({})
      setCreateModalOpen(false)
      setStatus(`Created station: ${name}`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create station')
    } finally {
      setBusy(false)
    }
  }

  async function saveStation(event: FormEvent) {
    event.preventDefault()
    if (!editingStation || !editingProvider) return
    const coverFileToSave = selectedCoverFile
    setBusy(true)
    setError('')
    setStatus('')
    try {
      let updated = await api.updateStation(editingStation.id, {
        name: editingName.trim() || editingStation.name,
        station_type: editingProvider.station_type,
        config: editingConfig,
      })
      if (coverFileToSave) {
        setStatus(`Uploading station cover: ${coverFileToSave.name}`)
        updated = withFreshCoverUrl(await api.uploadStationCover(updated.id, coverFileToSave))
      }
      setStations((existing) => existing.map((station) => station.id === updated.id ? updated : station))
      clearPendingCover()
      setEditingStation(null)
      setStatus(coverFileToSave ? `Updated station and cover: ${updated.name}` : `Updated station: ${updated.name}`)
      if (!coverFileToSave) await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update station')
    } finally {
      setBusy(false)
    }
  }

  async function deleteStation(station: Station) {
    if (!confirm(`Delete station "${station.name}"?`)) return
    setBusy(true)
    setError('')
    setStatus('')
    try {
      await api.deleteStation(station.id)
      if (editingStation?.id === station.id) setEditingStation(null)
      setStatus(`Deleted station: ${station.name}`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete station')
    } finally {
      setBusy(false)
    }
  }

  async function playStation(station: Station) {
    setStartingStation(station)
    setError('')
    try {
      await player.run(() => api.playStation(station.id), 'play')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start station')
    } finally {
      setStartingStation(null)
    }
  }

  async function uploadCover(file: File | null = selectedCoverFile) {
    if (!editingStation || !file) return
    setCoverBusy(true)
    setError('')
    setStatus('')
    try {
      const updated = withFreshCoverUrl(await api.uploadStationCover(editingStation.id, file))
      setEditingStation(updated)
      setStations((existing) => existing.map((station) => station.id === updated.id ? updated : station))
      clearPendingCover()
      setStatus(`Updated cover for ${updated.name}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not upload station cover')
    } finally {
      setCoverBusy(false)
    }
  }

  async function removeCustomCover() {
    if (!editingStation) return
    setCoverBusy(true)
    setError('')
    setStatus('')
    try {
      const updated = await api.deleteStationCover(editingStation.id)
      clearPendingCover()
      setEditingStation(updated)
      setStatus(`Removed custom cover for ${updated.name}`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove custom station cover')
    } finally {
      setCoverBusy(false)
    }
  }

  async function reloadTypes() {
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const rows = await api.reloadStationTypes()
      setProviders(rows)
      setStatus(`Reloaded ${rows.length} station type${rows.length === 1 ? '' : 's'}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reload station types')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page-stack station-page-redesign station-provider-page">
      {startingStation ? (
        <div className="station-start-modal-backdrop" role="status" aria-live="polite">
          <div className="station-start-modal">
            <div className="station-start-spinner" aria-hidden="true" />
            <div>
              <strong>Starting station</strong>
              <span>{startingStation.name}</span>
              <p>Helix is picking the first track and preparing playback.</p>
            </div>
          </div>
        </div>
      ) : null}

      <section className="stations-hero">
        <div className="stations-hero-copy">
          <h1>Stations</h1>
          <p>Build stations from StationProviders. Built-in and custom providers announce their own options, and Helix handles queueing, playback, and fulfillment.</p>
        </div>
        <div className="station-stats">
          <StationStat icon="▥" value={stations.length} label="Stations" />
          <StationStat icon="◉" value={visibleProviders.length} label="Types" />
          <StationStat icon="⚙" value={visibleProviders.filter((provider) => !provider.builtin).length} label="Custom" />
        </div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}
      {capabilities?.subsonic_configured === false ? <div className="info-banner">Subsonic is not configured. Library-only station mode is hidden and stations will use discovery playback.</div> : null}

      <section className="station-actions-panel">
        <div>
          <strong>Create and tune stations</strong>
          <span className="muted">Station setup now opens in focused modals so the station list stays clean.</span>
        </div>
        <div className="station-toolbar-actions">
          <button type="button" className="primary" onClick={openCreateModal} disabled={busy || !visibleProviders.length}>Create station</button>
          <button type="button" onClick={reloadTypes} disabled={busy}>Reload types</button>
        </div>
      </section>

      {isCreateModalOpen ? (
        <div className="station-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="station-create-title" onMouseDown={(event) => { if (event.target === event.currentTarget) closeCreateModal() }}>
          <section className="station-modal station-create-modal">
            <div className="station-modal-header">
              <div>
                <h2 id="station-create-title">Create station</h2>
                <p className="muted">Choose a provider, then fill in the options that provider exposes.</p>
              </div>
              <button type="button" className="icon-button compact-action" onClick={closeCreateModal} disabled={busy} aria-label="Close create station">×</button>
            </div>

            <div className="station-modal-body">
              <div className="station-type-picker station-type-picker-modal" role="list" aria-label="Station types">
                {visibleProviders.map((provider) => (
                  <button
                    type="button"
                    role="listitem"
                    key={provider.station_type}
                    className={`station-type-card ${selectedType === provider.station_type ? 'active' : ''}`}
                    onClick={() => chooseProvider(provider.station_type)}
                  >
                    <span>{provider.display_name}</span>
                    <small>{provider.description}</small>
                    <em>{provider.builtin ? 'Built-in' : 'Custom'} · {provider.version || '1.0.0'}</em>
                  </button>
                ))}
              </div>

              {selectedProvider ? (
                <form className="station-config-form station-modal-form" onSubmit={createStation}>
                  <label className="station-config-field station-name-field">
                    <span className="station-config-label">Station name</span>
                    <small>Optional. If left blank, Helix uses the provider name.</small>
                    <input value={stationName} onChange={(event) => setStationName(event.target.value)} placeholder="My station" />
                  </label>
                  <StationConfigForm provider={selectedProvider} config={config} onChange={setConfig} />
                  <div className="station-modal-footer">
                    <button type="button" className="ghost" onClick={closeCreateModal} disabled={busy}>Cancel</button>
                    <button className="primary" disabled={busy}>Create station</button>
                  </div>
                </form>
              ) : (
                <div className="info-banner">
                  {visibleProviders.length ? 'Choose a station provider to start configuring a new station.' : 'No station providers are available.'}
                </div>
              )}
            </div>
          </section>
        </div>
      ) : null}

      <section className="station-toolbar" aria-label="Station view controls">
        <div>
          <strong>Your stations</strong>
          <span className="muted">Play, tune, or delete stations created from provider types.</span>
        </div>
        <div className="station-toolbar-actions">
          <label>
            <span>Sort by</span>
            <select aria-label="Sort stations" value={sortMode} onChange={(event) => setSortMode(event.target.value)}>
              <option value="recent">Recently Updated</option>
              <option value="name">A–Z</option>
              <option value="created">Created</option>
            </select>
          </label>
        </div>
      </section>

      <div className="station-grid-redesign">
        {sortedStations.map((station) => {
          const provider = providerByType.get(station.station_type || '')
          return (
            <article className="station-card-redesign" key={station.id}>
              <div className="station-art-wrap">
                <Artwork src={station.cover_url || station.thumbnail_url || `/api/stations/${station.id}/cover`} alt={station.name} size="lg" />
                <button className="station-floating-play" type="button" onClick={() => void playStation(station)} disabled={Boolean(startingStation)} aria-label={`Play ${station.name}`}>
                  <IconPlay />
                </button>
              </div>
              <div className="station-card-body">
                <span className="station-type-badge">{providerLabel(provider, station.station_type)}</span>
                <h3>{station.name}</h3>
                <p className="muted">{configSummary(station, provider)}</p>
                <div className="station-card-actions station-card-actions-provider">
                  <button className="primary station-play-button" onClick={() => void playStation(station)} disabled={Boolean(startingStation)}>
                    <IconPlay /> Play
                  </button>
                  <button className="ghost station-edit-placeholder" type="button" onClick={() => startEditing(station)}>Tune</button>
                  <button className="ghost danger station-delete-button" type="button" onClick={() => void deleteStation(station)} disabled={busy}>Delete</button>
                </div>
              </div>
            </article>
          )
        })}
      </div>

      {editingStation && editingProvider ? (
        <div className="station-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="station-tune-title" onMouseDown={(event) => { if (event.target === event.currentTarget) closeEditor() }}>
          <section className="station-modal station-tune-modal">
            <div className="station-modal-header">
              <div>
                <h2 id="station-tune-title">Tune station</h2>
                <p className="muted">Editing {editingStation.name}</p>
              </div>
              <button type="button" className="icon-button compact-action" onClick={closeEditor} disabled={busy || coverBusy} aria-label="Close tune station">×</button>
            </div>

            <div className="station-modal-body">
              <form className="station-config-form station-modal-form" onSubmit={saveStation}>
                <label className="station-config-field station-name-field">
                  <span className="station-config-label">Station name</span>
                  <input value={editingName} onChange={(event) => setEditingName(event.target.value)} />
                </label>
                <div className="station-config-field station-cover-field">
                  <span className="station-config-label">Station cover</span>
                  <small>Recommended: square image. PNG, JPG, or WebP. Non-square images are center-cropped.</small>
                  <div className="station-cover-editor">
                    <Artwork src={selectedCoverPreviewUrl || editingStation.cover_url || editingStation.thumbnail_url || `/api/stations/${editingStation.id}/cover`} alt={editingStation.name} size="md" />
                    <div className="station-cover-actions">
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        disabled={coverBusy || busy}
                        onChange={(event) => choosePendingCover(event.target.files?.[0])}
                      />
                      {selectedCoverFile ? (
                        <div className="station-cover-pending">
                          <span>Selected: {selectedCoverFile.name}</span>
                          <button type="button" onClick={() => void uploadCover()} disabled={coverBusy || busy}>Upload cover now</button>
                          <button type="button" className="ghost" onClick={clearPendingCover} disabled={coverBusy || busy}>Clear selection</button>
                        </div>
                      ) : null}
                      <button type="button" onClick={() => void removeCustomCover()} disabled={coverBusy || busy || !editingStation.has_custom_cover}>Remove custom cover</button>
                      <small className="muted">Upload now, or save the station to apply it with your other changes.</small>
                    </div>
                  </div>
                </div>
                <label className="station-config-field station-name-field">
                  <span className="station-config-label">Station provider</span>
                  <small>Changing provider will rebuild the configurable option set.</small>
                  <select value={editingType} onChange={(event) => chooseEditingProvider(event.target.value)}>
                    {visibleProviders.map((provider) => <option key={provider.station_type} value={provider.station_type}>{provider.display_name}</option>)}
                  </select>
                </label>
                <StationConfigForm provider={editingProvider} config={editingConfig} onChange={setEditingConfig} />
                <div className="station-modal-footer">
                  <button type="button" className="ghost" onClick={closeEditor} disabled={busy || coverBusy}>Cancel</button>
                  <button className="primary" disabled={busy || coverBusy}>{selectedCoverFile ? 'Save station + cover' : 'Save station'}</button>
                  <button type="button" className="danger" onClick={() => void deleteStation(editingStation)} disabled={busy}>Delete station</button>
                </div>
              </form>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  )
}
