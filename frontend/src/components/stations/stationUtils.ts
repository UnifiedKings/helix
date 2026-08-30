import type { Capabilities, Station, StationConfigOption, StationProviderInfo } from '../../api/types'

export type StationConfig = Record<string, unknown>

export function optionDefault(option: StationConfigOption): unknown {
  if (option.default !== undefined && option.default !== null) return option.default
  if (option.type === 'boolean') return false
  if (option.type === 'number' || option.type === 'integer') return option.min ?? 0
  if (option.type === 'multiselect' || option.type === 'artist_search' || option.type === 'track_search') return []
  return ''
}

export function configFromProvider(provider: StationProviderInfo, existing?: StationConfig): StationConfig {
  const next: StationConfig = {}
  for (const option of provider.config_options ?? []) next[option.key] = existing && existing[option.key] !== undefined ? existing[option.key] : optionDefault(option)
  if (provider.station_type === 'song_radio' && existing) {
    for (const key of ['seed_type', 'seed_title', 'seed_artist', 'seed_video_id', 'seed_album'] as const) if (existing[key] !== undefined) next[key] = existing[key]

    // Migrate the old Safe/Balanced/Deep Song Radio control into the new
    // explicit pool-size and recommendation-bias controls. This preserves the
    // exact behavior existing stations had until the user chooses new values.
    const legacyDepth = String(existing.discovery_depth || '').trim().toLowerCase()
    const legacyValues: Record<string, { poolSize: number; rankBias: number }> = {
      safe: { poolSize: 20, rankBias: 2.0 },
      balanced: { poolSize: 50, rankBias: 1.15 },
      deep: { poolSize: 100, rankBias: 0.55 },
    }
    const legacy = legacyValues[legacyDepth]
    if (legacy) {
      if (existing.candidate_pool_size === undefined) next.candidate_pool_size = legacy.poolSize
      if (existing.top_recommendation_bias === undefined) next.top_recommendation_bias = legacy.rankBias
    }
  }
  if (provider.station_type === 'similar_artist' && existing) {
    for (const key of ['seed_type', 'seed_artist', 'seed_artist_id'] as const) if (existing[key] !== undefined) next[key] = existing[key]

    // Migrate the old Safe/Balanced/Deep Similar Artist control into the new
    // explicit breadth controls so existing stations show the values they were
    // already using instead of silently displaying the new defaults.
    const legacyDepth = String(existing.discovery_depth || '').trim().toLowerCase()
    const legacyValues: Record<string, { relatedArtists: number; songsPerArtist: number }> = {
      safe: { relatedArtists: 12, songsPerArtist: 8 },
      balanced: { relatedArtists: 35, songsPerArtist: 15 },
      deep: { relatedArtists: 100, songsPerArtist: 50 },
    }
    const legacy = legacyValues[legacyDepth]
    if (legacy) {
      if (existing.related_artist_limit === undefined) next.related_artist_limit = legacy.relatedArtists
      if (existing.popular_track_pool_size === undefined) next.popular_track_pool_size = legacy.songsPerArtist
    }
  }
  return next
}

export function providerForCapabilities(provider: StationProviderInfo, capabilities: Capabilities | null): StationProviderInfo {
  if (capabilities?.subsonic_configured !== false) return provider
  return { ...provider, config_options: (provider.config_options ?? []).map((option) => option.key !== 'source_mode' ? option : {
    ...option,
    default: 'prefer_library',
    choices: (option.choices ?? []).filter((choice) => String(choice.value) !== 'library_only'),
    description: 'Subsonic is not configured, so this station can use discovery tracks only.',
  }) }
}

export function withFreshCoverUrl(station: Station): Station {
  const baseUrl = station.cover_url || station.thumbnail_url || `/api/stations/${encodeURIComponent(station.id)}/cover`
  const freshUrl = `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}clientCoverBust=${Date.now()}`
  return { ...station, cover_url: freshUrl, thumbnail_url: freshUrl, has_custom_cover: true }
}

export function providerLabel(provider?: StationProviderInfo, fallback?: string) {
  return provider?.display_name || fallback || 'Unknown station type'
}

export function relativeUpdatedTime(value?: string | null) {
  if (!value) return 'Updated recently'
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return 'Updated recently'
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000))
  if (seconds < 60) return 'Updated just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `Updated ${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `Updated ${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `Updated ${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `Updated ${months}mo ago`
  return `Updated ${Math.floor(days / 365)}y ago`
}

export function configSummary(station: Station, provider?: StationProviderInfo) {
  const config = station.config ?? {}
  if ((station.station_type || provider?.station_type) === 'song_radio') {
    const title = String(config.seed_title || station.seed_title || '').trim()
    const artist = String(config.seed_artist || station.seed_artist || '').trim()
    if (title && artist) return `${artist} — ${title}`
    if (title || artist) return title || artist
  }
  const seed = String(config.seed_artist || station.seed_artist || config.seed_title || station.seed_title || '').trim()
  if (seed) return seed
  const firstRequired = provider?.config_options?.find((option) => option.required)
  if (firstRequired && config[firstRequired.key]) {
    const value = config[firstRequired.key]
    if (firstRequired.type === 'artist_search' && Array.isArray(value)) {
      const names = value.map((item) => {
        if (typeof item === 'string') return item.trim()
        if (item && typeof item === 'object') return String((item as { name?: unknown }).name || '').trim()
        return ''
      }).filter(Boolean)
      if (names.length) return names.join(', ')
    }
    if (firstRequired.type === 'track_search' && Array.isArray(value)) {
      const labels = value.map((item) => {
        if (!item || typeof item !== 'object') return ''
        const row = item as { title?: unknown; artist?: unknown }
        const title = String(row.title || '').trim()
        const artist = String(row.artist || '').trim()
        return title && artist ? `${artist} — ${title}` : title || artist
      }).filter(Boolean)
      if (labels.length) return labels.join(', ')
    }
    return String(value)
  }
  return 'Configured station'
}
