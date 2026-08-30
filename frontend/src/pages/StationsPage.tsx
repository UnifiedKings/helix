import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Capabilities, Station, StationProviderInfo } from '../api/types'
import { Artwork } from '../components/Artwork'
import { StationCard, StationConfigForm, StationStat, configFromProvider, providerForCapabilities, withFreshCoverUrl, type StationConfig } from '../components/stations'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

export function StationsPage() {
  const player = useOutletContext<PlayerContext>()
  const [searchParams, setSearchParams] = useSearchParams()
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

  useEffect(() => {
    const editStationId = searchParams.get('edit')
    if (!editStationId || !stations.length || !visibleProviders.length || editingStation) return
    const target = stations.find((station) => station.id === editStationId)
    if (!target) return

    const stationType = target.station_type || visibleProviders[0]?.station_type || ''
    const provider = providerByType.get(stationType)
    setEditingStation(target)
    setEditingName(target.name)
    setEditingType(stationType)
    setEditingConfig(provider ? configFromProvider(provider, target.config) : { ...(target.config ?? {}) })
    clearPendingCover()
    setStatus('')
    setError('')

    const next = new URLSearchParams(searchParams)
    next.delete('edit')
    setSearchParams(next, { replace: true })
  }, [editingStation, providerByType, searchParams, setSearchParams, stations, visibleProviders])

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
        <h1>Stations</h1>
        <div className="stations-meta-row">
          <div className="station-stats">
            <StationStat icon="▥" value={stations.length} label="Stations" />
            <StationStat icon="♫" value={visibleProviders.length} label="Types" />
            <StationStat icon="⌘" value={visibleProviders.filter((provider) => !provider.builtin).length} label="Custom" />
          </div>
          <div className="station-toolbar-actions station-header-actions">
            <button type="button" className="primary" onClick={openCreateModal} disabled={busy || !visibleProviders.length}>+ Create station</button>
            <button type="button" onClick={reloadTypes} disabled={busy}>Reload types</button>
          </div>
        </div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}
      {capabilities?.subsonic_configured === false ? <div className="info-banner">Subsonic is not configured. Library-only station mode is hidden and stations will use discovery playback.</div> : null}

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
        <div className="station-toolbar-actions">
          <label>
            <span>Sort by</span>
            <select aria-label="Sort stations" value={sortMode} onChange={(event) => setSortMode(event.target.value)}>
              <option value="recent">Recently updated</option>
              <option value="name">A–Z</option>
              <option value="created">Created</option>
            </select>
          </label>
        </div>
      </section>

      <div className="station-grid-redesign">
        {sortedStations.map((station) => (
          <StationCard
            key={station.id}
            station={station}
            provider={providerByType.get(station.station_type || '')}
            busy={busy}
            starting={Boolean(startingStation)}
            onPlay={() => void playStation(station)}
            onEdit={() => startEditing(station)}
            onDelete={() => void deleteStation(station)}
          />
        ))}
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
              <form className="station-config-form station-modal-form station-tune-form" onSubmit={saveStation}>
                <div className="station-tune-layout">
                  <aside className="station-tune-overview" aria-label="Station overview">
                    <label className="station-tune-flat-field station-name-field">
                      <span className="station-config-label">Station name</span>
                      <input value={editingName} onChange={(event) => setEditingName(event.target.value)} />
                    </label>
                    <div className="station-tune-flat-field station-cover-field">
                      <span className="station-config-label">Station cover</span>
                      <small>Square PNG, JPG, or WebP works best.</small>
                      <div className="station-cover-editor station-cover-editor-compact">
                        <Artwork src={selectedCoverPreviewUrl || editingStation.cover_url || editingStation.thumbnail_url || `/api/stations/${editingStation.id}/cover`} alt={editingStation.name} size="md" />
                        <div className="station-cover-actions">
                          <div className="station-cover-file-row">
                            <label className={`station-cover-file-button ${(coverBusy || busy) ? 'is-disabled' : ''}`}>
                              <span>{selectedCoverFile ? 'Change file' : 'Choose file'}</span>
                              <input
                                className="station-cover-file-input"
                                type="file"
                                accept="image/png,image/jpeg,image/webp"
                                disabled={coverBusy || busy}
                                onChange={(event) => choosePendingCover(event.target.files?.[0])}
                              />
                            </label>
                            <span className={`station-cover-file-name ${selectedCoverFile ? 'has-file' : ''}`} title={selectedCoverFile?.name || 'No file selected'}>
                              {selectedCoverFile?.name || 'No file selected'}
                            </span>
                          </div>
                          {selectedCoverFile ? (
                            <div className="station-cover-pending-actions">
                              <button type="button" className="station-cover-upload-now" onClick={() => void uploadCover()} disabled={coverBusy || busy}>Upload now</button>
                              <button type="button" className="ghost station-cover-clear-pending" onClick={clearPendingCover} disabled={coverBusy || busy}>Clear</button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="ghost station-cover-remove"
                              onClick={() => void removeCustomCover()}
                              disabled={coverBusy || busy || !editingStation.has_custom_cover}
                            >
                              Remove custom cover
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                    <label className="station-tune-flat-field station-name-field">
                      <span className="station-config-label">Provider</span>
                      <small>Changing provider rebuilds the available options.</small>
                      <select value={editingType} onChange={(event) => chooseEditingProvider(event.target.value)}>
                        {visibleProviders.map((provider) => <option key={provider.station_type} value={provider.station_type}>{provider.display_name}</option>)}
                      </select>
                    </label>
                    <div className="station-tune-provider-note"><strong>{editingProvider.display_name}</strong><span>{editingProvider.builtin ? 'Built-in provider' : 'Custom provider'} · v{editingProvider.version || '1.0.0'}</span><p>{editingProvider.description}</p></div>
                  </aside>
                  <section className="station-tune-options" aria-label="Station configuration">
                    <StationConfigForm provider={editingProvider} config={editingConfig} onChange={setEditingConfig} />
                  </section>
                </div>
                <div className="station-modal-footer station-tune-footer">
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
