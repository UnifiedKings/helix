import { useState } from 'react'
import { api } from '../../api/client'
import type { SearchArtist, SearchSong, StationConfigOption, StationProviderInfo } from '../../api/types'
import { optionDefault, type StationConfig } from './stationUtils'

function coerceConfigValue(option: StationConfigOption, raw: string | boolean | string[]): unknown {
  if (option.type === 'boolean') return Boolean(raw)
  if (option.type === 'integer') { const parsed = Number.parseInt(String(raw), 10); return Number.isFinite(parsed) ? parsed : optionDefault(option) }
  if (option.type === 'number') { const parsed = Number.parseFloat(String(raw)); return Number.isFinite(parsed) ? parsed : optionDefault(option) }
  if (option.type === 'multiselect') return Array.isArray(raw) ? raw : []
  return String(raw)
}

function ConfigOptionField({ option, value, onChange }: { option: StationConfigOption; value: unknown; onChange: (value: unknown) => void }) {
  const id = `station-config-${option.key}`
  const commonProps = { id, name: option.key }
  let control
  if (option.type === 'boolean') control = <label className="station-checkbox-field"><input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} /><span>{Boolean(value) ? 'Enabled' : 'Disabled'}</span></label>
  else if (option.type === 'select') control = <select {...commonProps} value={String(value ?? '')} onChange={(event) => onChange(event.target.value)}>{(option.choices ?? []).map((choice) => <option key={String(choice.value)} value={String(choice.value)}>{choice.label ?? String(choice.value)}</option>)}</select>
  else if (option.type === 'multiselect') control = <select {...commonProps} multiple value={Array.isArray(value) ? value.map(String) : []} onChange={(event) => onChange(Array.from(event.target.selectedOptions).map((item) => item.value))}>{(option.choices ?? []).map((choice) => <option key={String(choice.value)} value={String(choice.value)}>{choice.label ?? String(choice.value)}</option>)}</select>
  else if (option.type === 'textarea') control = <textarea {...commonProps} value={String(value ?? '')} onChange={(event) => onChange(event.target.value)} rows={4} />
  else if (option.type === 'number' || option.type === 'integer') control = <input {...commonProps} type="number" min={option.min} max={option.max} step={option.step ?? (option.type === 'integer' ? 1 : 0.05)} value={String(value ?? '')} onChange={(event) => onChange(coerceConfigValue(option, event.target.value))} />
  else control = <input {...commonProps} value={String(value ?? '')} onChange={(event) => onChange(event.target.value)} />
  return <label className="station-config-field" htmlFor={id}><span className="station-config-label">{option.label}{option.required ? <strong>Required</strong> : null}</span>{option.description ? <small>{option.description}</small> : null}{control}</label>
}

function SongRadioSeedPicker({ config, onChange }: { config: StationConfig; onChange: (config: StationConfig) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchSong[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const seedTitle = String(config.seed_title || '').trim(); const seedArtist = String(config.seed_artist || '').trim()
  async function runSearch() {
    const q = query.trim(); if (!q) return
    setSearching(true); setSearchError('')
    try { const payload = await api.search(q, 'ytmusic'); setResults((payload.songs ?? []).slice(0, 10)); if (!(payload.songs ?? []).length) setSearchError('No YouTube Music songs found.') }
    catch (err) { setSearchError(err instanceof Error ? err.message : 'Could not search YouTube Music') }
    finally { setSearching(false) }
  }
  function chooseSeed(song: SearchSong) {
    const videoId = String(song.video_id || song.videoId || song.yt_video_id || '').trim()
    onChange({ ...config, seed_type: 'track', seed_title: song.title, seed_artist: song.artist, seed_video_id: videoId, seed_album: song.album || '' })
    setResults([]); setQuery(''); setSearchError('')
  }
  return <div className="station-song-seed-picker"><div className="station-config-field"><span className="station-config-label">Seed song <strong>Required</strong></span><small>Search YouTube Music and choose the exact song this radio should be built around.</small>{seedTitle && seedArtist ? <div className="station-song-seed-selected"><strong>{seedTitle}</strong><span>{seedArtist}</span></div> : null}<div className="station-song-seed-search"><input value={query} placeholder={seedTitle ? 'Search to change seed song' : 'Search for a song'} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void runSearch() } }} /><button type="button" className="btn-secondary" disabled={searching || !query.trim()} onClick={() => void runSearch()}>{searching ? 'Searching…' : 'Search'}</button></div>{searchError ? <small className="error-text">{searchError}</small> : null}{results.length ? <div className="station-song-seed-results">{results.map((song) => { const videoId = String(song.video_id || song.videoId || song.yt_video_id || ''); return <button type="button" className="station-song-seed-result" key={`${videoId}:${song.title}:${song.artist}`} onClick={() => chooseSeed(song)}><strong>{song.title}</strong><span>{song.artist}{song.album ? ` • ${song.album}` : ''}</span></button> })}</div> : null}</div></div>
}

function SimilarArtistSeedPicker({ config, onChange }: { config: StationConfig; onChange: (config: StationConfig) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchArtist[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const seedArtist = String(config.seed_artist || '').trim()

  async function runSearch() {
    const q = query.trim(); if (!q) return
    setSearching(true); setSearchError('')
    try {
      const payload = await api.searchArtists(q)
      setResults((payload.artists ?? []).slice(0, 10))
      if (!(payload.artists ?? []).length) setSearchError('No YouTube Music artists found.')
    } catch (err) { setSearchError(err instanceof Error ? err.message : 'Could not search YouTube Music') }
    finally { setSearching(false) }
  }

  function chooseSeed(artist: SearchArtist) {
    const browseId = String(artist.browse_id || artist.artist_id || '').trim()
    onChange({ ...config, seed_type: 'artist', seed_artist: artist.name, seed_artist_id: browseId })
    setResults([]); setQuery(''); setSearchError('')
  }

  return <div className="station-song-seed-picker"><div className="station-config-field"><span className="station-config-label">Seed artist <strong>Required</strong></span><small>Search YouTube Music and choose the exact artist this radio should be built around.</small>{seedArtist ? <div className="station-song-seed-selected"><strong>{seedArtist}</strong></div> : null}<div className="station-song-seed-search"><input value={query} placeholder={seedArtist ? 'Search to change seed artist' : 'Search for an artist'} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void runSearch() } }} /><button type="button" className="btn-secondary" disabled={searching || !query.trim()} onClick={() => void runSearch()}>{searching ? 'Searching…' : 'Search'}</button></div>{searchError ? <small className="error-text">{searchError}</small> : null}{results.length ? <div className="station-song-seed-results">{results.map((artist) => { const browseId = String(artist.browse_id || artist.artist_id || ''); return <button type="button" className="station-song-seed-result" key={`${browseId}:${artist.name}`} onClick={() => chooseSeed(artist)}><strong>{artist.name}</strong>{artist.subscriber_count ? <span>{artist.subscriber_count}</span> : null}</button> })}</div> : null}</div></div>
}

export function StationConfigForm({ provider, config, onChange }: { provider: StationProviderInfo; config: StationConfig; onChange: (config: StationConfig) => void }) {
  if (!provider.config_options?.length) return <div className="info-banner">This station type does not expose configurable options.</div>
  return <div className="station-config-grid">{provider.station_type === 'song_radio' ? <SongRadioSeedPicker config={config} onChange={onChange} /> : null}{provider.station_type === 'similar_artist' ? <SimilarArtistSeedPicker config={config} onChange={onChange} /> : null}{provider.config_options.filter((option) => !(provider.station_type === 'similar_artist' && option.key === 'seed_artist')).map((option) => <ConfigOptionField key={option.key} option={option} value={config[option.key] ?? optionDefault(option)} onChange={(value) => onChange({ ...config, [option.key]: value })} />)}</div>
}
