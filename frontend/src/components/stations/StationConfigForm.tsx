import { useState, type ReactNode } from 'react'
import { api } from '../../api/client'
import { Artwork } from '../Artwork'
import type { SearchArtist, SearchSong, StationConfigOption, StationProviderInfo } from '../../api/types'
import { optionDefault, type StationConfig } from './stationUtils'


function seedArtwork(item: SearchSong | SearchArtist) {
  if ('title' in item) {
    return item.art_url || item.thumbnail_url || item.thumbnail || item.thumbnails?.[item.thumbnails.length - 1]?.url || item.thumbnails?.[0]?.url || ''
  }
  return item.art_url || item.thumbnail_url || ''
}


function songAlbumName(song: SearchSong): string {
  const raw = song as SearchSong & {
    album?: unknown
    album_name?: unknown
    albumName?: unknown
  }
  const album = raw.album
  if (typeof album === 'string') return album.trim()
  if (album && typeof album === 'object') {
    const record = album as { name?: unknown; title?: unknown }
    if (typeof record.name === 'string' && record.name.trim()) return record.name.trim()
    if (typeof record.title === 'string' && record.title.trim()) return record.title.trim()
  }
  if (typeof raw.album_name === 'string' && raw.album_name.trim()) return raw.album_name.trim()
  if (typeof raw.albumName === 'string' && raw.albumName.trim()) return raw.albumName.trim()
  return ''
}

type ArtistSeedSelection = {
  name: string
  browse_id?: string
  art_url?: string
  thumbnail_url?: string
}

type TrackSeedSelection = {
  title: string
  artist: string
  album?: string
  video_id?: string
  art_url?: string
  thumbnail_url?: string
}

function artistSelections(value: unknown): ArtistSeedSelection[] {
  const rawItems = Array.isArray(value)
    ? value
    : typeof value === 'string'
      ? value.replace(/\r/g, '\n').replace(/,/g, '\n').split('\n')
      : []
  const rows: ArtistSeedSelection[] = []
  const seen = new Set<string>()
  for (const item of rawItems) {
    const row = typeof item === 'string'
      ? { name: item.trim() }
      : item && typeof item === 'object'
        ? {
            name: String((item as { name?: unknown; artist?: unknown }).name || (item as { artist?: unknown }).artist || '').trim(),
            browse_id: String((item as { browse_id?: unknown; artist_id?: unknown }).browse_id || (item as { artist_id?: unknown }).artist_id || '').trim() || undefined,
            art_url: String((item as { art_url?: unknown }).art_url || '').trim() || undefined,
            thumbnail_url: String((item as { thumbnail_url?: unknown }).thumbnail_url || '').trim() || undefined,
          }
        : { name: '' }
    if (!row.name) continue
    const key = (row.browse_id || row.name).toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    rows.push(row)
  }
  return rows
}

function trackSelections(value: unknown): TrackSeedSelection[] {
  if (!Array.isArray(value)) return []
  const rows: TrackSeedSelection[] = []
  const seen = new Set<string>()
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const raw = item as Record<string, unknown>
    const title = String(raw.title || '').trim()
    const artist = String(raw.artist || '').trim()
    if (!title || !artist) continue
    const videoId = String(raw.video_id || raw.videoId || raw.yt_video_id || '').trim()
    const row: TrackSeedSelection = {
      title,
      artist,
      album: String(raw.album || raw.album_name || raw.albumName || '').trim() || undefined,
      video_id: videoId || undefined,
      art_url: String(raw.art_url || '').trim() || undefined,
      thumbnail_url: String(raw.thumbnail_url || raw.thumbnail || '').trim() || undefined,
    }
    const key = (videoId || `${title}|${artist}`).toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    rows.push(row)
  }
  return rows
}

function ArtistSearchConfigField({ option, value, onChange }: { option: StationConfigOption; value: unknown; onChange: (value: unknown) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchArtist[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const selected = artistSelections(value)
  const maxItems = Math.max(1, Number(option.max_items ?? (option as StationConfigOption & { maxItems?: number }).maxItems ?? 1))

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

  function chooseArtist(artist: SearchArtist) {
    const browseId = String(artist.browse_id || artist.artist_id || '').trim()
    const next: ArtistSeedSelection = {
      name: artist.name,
      browse_id: browseId || undefined,
      art_url: artist.art_url || undefined,
      thumbnail_url: artist.thumbnail_url || undefined,
    }
    const duplicate = selected.some((item) => (browseId && item.browse_id === browseId) || item.name.toLowerCase() === artist.name.toLowerCase())
    if (duplicate) return
    if (maxItems === 1) onChange([next])
    else if (selected.length < maxItems) onChange([...selected, next])
    setResults([]); setQuery(''); setSearchError('')
  }

  function removeArtist(index: number) {
    onChange(selected.filter((_, selectedIndex) => selectedIndex !== index))
  }

  return <div className="station-song-seed-picker station-search-config-field"><div className="station-config-field"><span className="station-config-label">{option.label}{option.required ? <strong>Required</strong> : null}</span>{option.description ? <small>{option.description}</small> : null}{selected.length ? <div className="station-seed-selection-list">{selected.map((artist, index) => <div className="station-seed-selection" key={`${artist.browse_id || artist.name}:${index}`}><Artwork src={artist.art_url || artist.thumbnail_url || ''} alt={artist.name} size="sm" /><span className="station-seed-selection-copy"><strong>{artist.name}</strong></span><button type="button" className="station-seed-selection-remove" aria-label={`Remove ${artist.name}`} onClick={() => removeArtist(index)}>×</button></div>)}</div> : null}<div className="station-song-seed-search"><input value={query} placeholder={selected.length ? 'Search to add another artist' : 'Search for an artist'} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void runSearch() } }} /><button type="button" className="btn-secondary" disabled={searching || !query.trim() || (maxItems > 1 && selected.length >= maxItems)} onClick={() => void runSearch()}>{searching ? 'Searching…' : 'Search'}</button></div><small className="station-seed-selection-count">{selected.length} / {maxItems} selected</small>{searchError ? <small className="error-text">{searchError}</small> : null}{results.length ? <div className="station-song-seed-results">{results.map((artist) => { const browseId = String(artist.browse_id || artist.artist_id || ''); const duplicate = selected.some((item) => (browseId && item.browse_id === browseId) || item.name.toLowerCase() === artist.name.toLowerCase()); return <button type="button" className="station-song-seed-result" disabled={duplicate || (maxItems > 1 && selected.length >= maxItems)} key={`${browseId}:${artist.name}`} onClick={() => chooseArtist(artist)}><Artwork src={seedArtwork(artist)} alt={artist.name} size="sm" /><span className="station-song-seed-result-copy"><strong>{artist.name}</strong>{duplicate ? <span>Already selected</span> : null}</span></button> })}</div> : null}</div></div>
}

function TrackSearchConfigField({ option, value, onChange }: { option: StationConfigOption; value: unknown; onChange: (value: unknown) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchSong[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const selected = trackSelections(value)
  const maxItems = Math.max(1, Number(option.max_items ?? (option as StationConfigOption & { maxItems?: number }).maxItems ?? 1))

  async function runSearch() {
    const q = query.trim(); if (!q) return
    setSearching(true); setSearchError('')
    try {
      const payload = await api.search(q, 'ytmusic')
      setResults((payload.songs ?? []).slice(0, 10))
      if (!(payload.songs ?? []).length) setSearchError('No YouTube Music songs found.')
    } catch (err) { setSearchError(err instanceof Error ? err.message : 'Could not search YouTube Music') }
    finally { setSearching(false) }
  }

  function chooseTrack(song: SearchSong) {
    const videoId = String(song.video_id || song.videoId || song.yt_video_id || '').trim()
    const next: TrackSeedSelection = {
      title: song.title,
      artist: song.artist,
      album: songAlbumName(song) || undefined,
      video_id: videoId || undefined,
      art_url: song.art_url || undefined,
      thumbnail_url: song.thumbnail_url || song.thumbnail || undefined,
    }
    const duplicate = selected.some((item) => (videoId && item.video_id === videoId) || `${item.title}|${item.artist}`.toLowerCase() === `${song.title}|${song.artist}`.toLowerCase())
    if (duplicate) return
    if (maxItems === 1) onChange([next])
    else if (selected.length < maxItems) onChange([...selected, next])
    setResults([]); setQuery(''); setSearchError('')
  }

  function removeTrack(index: number) {
    onChange(selected.filter((_, selectedIndex) => selectedIndex !== index))
  }

  return <div className="station-song-seed-picker station-search-config-field"><div className="station-config-field"><span className="station-config-label">{option.label}{option.required ? <strong>Required</strong> : null}</span>{option.description ? <small>{option.description}</small> : null}{selected.length ? <div className="station-seed-selection-list">{selected.map((track, index) => <div className="station-seed-selection" key={`${track.video_id || `${track.title}:${track.artist}`}:${index}`}><Artwork src={track.art_url || track.thumbnail_url || ''} alt={track.title} size="sm" /><span className="station-seed-selection-copy"><strong>{track.title}</strong><span>{track.artist}{track.album ? ` • ${track.album}` : ''}</span></span><button type="button" className="station-seed-selection-remove" aria-label={`Remove ${track.title}`} onClick={() => removeTrack(index)}>×</button></div>)}</div> : null}<div className="station-song-seed-search"><input value={query} placeholder={selected.length ? 'Search to add another track' : 'Search for a track'} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void runSearch() } }} /><button type="button" className="btn-secondary" disabled={searching || !query.trim() || (maxItems > 1 && selected.length >= maxItems)} onClick={() => void runSearch()}>{searching ? 'Searching…' : 'Search'}</button></div><small className="station-seed-selection-count">{selected.length} / {maxItems} selected</small>{searchError ? <small className="error-text">{searchError}</small> : null}{results.length ? <div className="station-song-seed-results">{results.map((song) => { const videoId = String(song.video_id || song.videoId || song.yt_video_id || ''); const duplicate = selected.some((item) => (videoId && item.video_id === videoId) || `${item.title}|${item.artist}`.toLowerCase() === `${song.title}|${song.artist}`.toLowerCase()); return <button type="button" className="station-song-seed-result" disabled={duplicate || (maxItems > 1 && selected.length >= maxItems)} key={`${videoId}:${song.title}:${song.artist}`} onClick={() => chooseTrack(song)}><Artwork src={seedArtwork(song)} alt={song.title} size="sm" /><span className="station-song-seed-result-copy"><strong>{song.title}</strong><span>{song.artist}{songAlbumName(song) ? ` • ${songAlbumName(song)}` : ''}{duplicate ? ' • Already selected' : ''}</span></span></button> })}</div> : null}</div></div>
}

function coerceConfigValue(option: StationConfigOption, raw: string | boolean | string[]): unknown {
  if (option.type === 'boolean') return Boolean(raw)
  if (option.type === 'integer') { const parsed = Number.parseInt(String(raw), 10); return Number.isFinite(parsed) ? parsed : optionDefault(option) }
  if (option.type === 'number') { const parsed = Number.parseFloat(String(raw)); return Number.isFinite(parsed) ? parsed : optionDefault(option) }
  if (option.type === 'multiselect') return Array.isArray(raw) ? raw : []
  return String(raw)
}

function ConfigOptionField({ option, value, onChange }: { option: StationConfigOption; value: unknown; onChange: (value: unknown) => void }) {
  if (option.type === 'artist_search') return <ArtistSearchConfigField option={option} value={value} onChange={onChange} />
  if (option.type === 'track_search') return <TrackSearchConfigField option={option} value={value} onChange={onChange} />
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
    onChange({ ...config, seed_type: 'track', seed_title: song.title, seed_artist: song.artist, seed_video_id: videoId, seed_album: songAlbumName(song) })
    setResults([]); setQuery(''); setSearchError('')
  }
  return <div className="station-song-seed-picker"><div className="station-config-field"><span className="station-config-label">Seed song <strong>Required</strong></span><small>Search YouTube Music and choose the exact song this radio should be built around.</small>{seedTitle && seedArtist ? <div className="station-song-seed-selected"><strong>{seedTitle}</strong><span>{seedArtist}</span></div> : null}<div className="station-song-seed-search"><input value={query} placeholder={seedTitle ? 'Search to change seed song' : 'Search for a song'} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void runSearch() } }} /><button type="button" className="btn-secondary" disabled={searching || !query.trim()} onClick={() => void runSearch()}>{searching ? 'Searching…' : 'Search'}</button></div>{searchError ? <small className="error-text">{searchError}</small> : null}{results.length ? <div className="station-song-seed-results">{results.map((song) => { const videoId = String(song.video_id || song.videoId || song.yt_video_id || ''); return <button type="button" className="station-song-seed-result" key={`${videoId}:${song.title}:${song.artist}`} onClick={() => chooseSeed(song)}><Artwork src={seedArtwork(song)} alt={song.title} size="sm" /><span className="station-song-seed-result-copy"><strong>{song.title}</strong><span>{song.artist}{songAlbumName(song) ? ` • ${songAlbumName(song)}` : ''}</span></span></button> })}</div> : null}</div></div>
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

  return <div className="station-song-seed-picker"><div className="station-config-field"><span className="station-config-label">Seed artist <strong>Required</strong></span><small>Search YouTube Music and choose the exact artist this radio should be built around.</small>{seedArtist ? <div className="station-song-seed-selected"><strong>{seedArtist}</strong></div> : null}<div className="station-song-seed-search"><input value={query} placeholder={seedArtist ? 'Search to change seed artist' : 'Search for an artist'} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void runSearch() } }} /><button type="button" className="btn-secondary" disabled={searching || !query.trim()} onClick={() => void runSearch()}>{searching ? 'Searching…' : 'Search'}</button></div>{searchError ? <small className="error-text">{searchError}</small> : null}{results.length ? <div className="station-song-seed-results">{results.map((artist) => { const browseId = String(artist.browse_id || artist.artist_id || ''); return <button type="button" className="station-song-seed-result" key={`${browseId}:${artist.name}`} onClick={() => chooseSeed(artist)}><Artwork src={seedArtwork(artist)} alt={artist.name} size="sm" /><span className="station-song-seed-result-copy"><strong>{artist.name}</strong>{artist.subscriber_count ? <span>{artist.subscriber_count}</span> : null}</span></button> })}</div> : null}</div></div>
}

function categoryTitle(option: StationConfigOption) {
  if (option.category_label?.trim()) return option.category_label.trim()
  const id = String(option.category || 'options').trim() || 'options'
  return id.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function StationConfigForm({ provider, config, onChange }: { provider: StationProviderInfo; config: StationConfig; onChange: (config: StationConfig) => void }) {
  const declared = (provider.config_options ?? [])
    .filter((option) => !(provider.station_type === 'similar_artist' && option.key === 'seed_artist'))
    .map((option) => provider.station_type === 'artist_collection' && option.key === 'seed_artists' && option.max_items == null
      ? { ...option, max_items: 100 }
      : option)

  const specialCategories: Array<{ id: string; label: string; order: number; content: ReactNode }> = []
  if (provider.station_type === 'song_radio') specialCategories.push({ id: 'seeds', label: 'Seeds', order: 10, content: <SongRadioSeedPicker config={config} onChange={onChange} /> })
  if (provider.station_type === 'similar_artist') specialCategories.push({ id: 'seeds', label: 'Seeds', order: 10, content: <SimilarArtistSeedPicker config={config} onChange={onChange} /> })

  if (!declared.length && !specialCategories.length) return <div className="info-banner">This station type does not expose configurable options.</div>

  const grouped = new Map<string, { id: string; label: string; order: number; options: StationConfigOption[] }>()
  for (const option of declared) {
    const id = String(option.category || 'options').trim() || 'options'
    const existing = grouped.get(id)
    if (existing) existing.options.push(option)
    else grouped.set(id, { id, label: categoryTitle(option), order: Number(option.category_order ?? 100), options: [option] })
  }
  const categories = [
    ...specialCategories.map((item) => ({ ...item, options: [] as StationConfigOption[] })),
    ...Array.from(grouped.values()),
  ].sort((a, b) => a.order - b.order || a.label.localeCompare(b.label))

  const [selectedCategory, setSelectedCategory] = useState(categories[0]?.id || 'options')
  const active = categories.find((category) => category.id === selectedCategory) ?? categories[0]

  return <div className="station-config-categorized">
    {categories.length > 1 ? <div className="station-config-tabs" role="tablist" aria-label="Station configuration categories">
      {categories.map((category) => <button type="button" role="tab" aria-selected={active?.id === category.id} className={active?.id === category.id ? 'active' : ''} key={category.id} onClick={() => setSelectedCategory(category.id)}>{category.label}</button>)}
    </div> : null}
    {active ? <section className="station-config-category" role="tabpanel">
      <div className="station-config-category-heading"><span>{active.label}</span><small>Options provided by {provider.display_name}.</small></div>
      <div className="station-config-grid">
        {specialCategories.find((item) => item.id === active.id)?.content}
        {active.options.sort((a, b) => Number(a.order ?? 100) - Number(b.order ?? 100)).map((option) => <ConfigOptionField key={option.key} option={option} value={config[option.key] ?? optionDefault(option)} onChange={(value) => onChange({ ...config, [option.key]: value })} />)}
      </div>
    </section> : null}
  </div>
}
