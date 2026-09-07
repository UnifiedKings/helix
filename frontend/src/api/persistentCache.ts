import { api } from './client'
import type { PlaybackHistoryFilters } from './types'

const STORAGE_PREFIX = 'helix:swr:v1:'
const CACHE_EVENT = 'helix:persistent-cache-update'
const MAX_CACHE_AGE_MS = 12 * 60 * 60 * 1000
const inFlight = new Map<string, Promise<unknown>>()
let installed = false

type CacheEntry<T> = {
  savedAt: number
  data: T
}

type CacheUpdateDetail = {
  key: string
}

function storage() {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function storageKey(key: string) {
  return `${STORAGE_PREFIX}${key}`
}

function readCache<T>(key: string): T | undefined {
  const store = storage()
  if (!store) return undefined

  try {
    const raw = store.getItem(storageKey(key))
    if (!raw) return undefined
    const entry = JSON.parse(raw) as CacheEntry<T>
    if (!entry || typeof entry.savedAt !== 'number') {
      store.removeItem(storageKey(key))
      return undefined
    }
    if (Date.now() - entry.savedAt > MAX_CACHE_AGE_MS) {
      store.removeItem(storageKey(key))
      return undefined
    }
    return entry.data
  } catch {
    try { store.removeItem(storageKey(key)) } catch { /* ignore */ }
    return undefined
  }
}

function writeCache<T>(key: string, data: T) {
  const store = storage()
  if (!store) return

  try {
    store.setItem(storageKey(key), JSON.stringify({
      savedAt: Date.now(),
      data,
    } satisfies CacheEntry<T>))
  } catch {
    // sessionStorage is only an optimization. A full/disabled store should
    // never make a Helix page fail to load.
  }
}

function sameValue(a: unknown, b: unknown) {
  if (Object.is(a, b)) return true
  try {
    return JSON.stringify(a) === JSON.stringify(b)
  } catch {
    return false
  }
}

function announceUpdate(key: string) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<CacheUpdateDetail>(CACHE_EVENT, {
    detail: { key },
  }))
}

function fetchDeduped<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const existing = inFlight.get(key)
  if (existing) return existing as Promise<T>

  const promise = fetcher().finally(() => {
    if (inFlight.get(key) === promise) inFlight.delete(key)
  })
  inFlight.set(key, promise)
  return promise
}

async function persistentSWR<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const cached = readCache<T>(key)

  if (cached !== undefined) {
    // Stale-while-revalidate: hand the page its saved response immediately,
    // then compare a fresh backend response in the background.
    void fetchDeduped(key, fetcher)
      .then((fresh) => {
        // Another caller may have updated this cache while our request was in
        // flight. Compare with the latest stored value before announcing.
        const latest = readCache<T>(key)
        if (sameValue(latest, fresh)) {
          writeCache(key, fresh)
          return
        }
        writeCache(key, fresh)
        announceUpdate(key)
      })
      .catch(() => {
        // Cached data remains usable when the backend refresh fails.
      })

    return cached
  }

  const fresh = await fetchDeduped(key, fetcher)
  writeCache(key, fresh)
  return fresh
}

export function invalidatePersistentCache(prefix: string, announce = false) {
  const store = storage()
  if (!store) return

  const fullPrefix = storageKey(prefix)
  const removedKeys: string[] = []
  for (let index = store.length - 1; index >= 0; index -= 1) {
    const key = store.key(index)
    if (!key?.startsWith(fullPrefix)) continue
    store.removeItem(key)
    removedKeys.push(key.slice(STORAGE_PREFIX.length))
  }

  if (announce) {
    for (const key of removedKeys) announceUpdate(key)
  }
}

export function clearPersistentCache() {
  const store = storage()
  if (!store) return

  for (let index = store.length - 1; index >= 0; index -= 1) {
    const key = store.key(index)
    if (key?.startsWith(STORAGE_PREFIX)) store.removeItem(key)
  }
}

function isDefaultHistoryRequest(filters: PlaybackHistoryFilters = {}) {
  const meaningfulEntries = Object.entries(filters).filter(([key, value]) => {
    if (value === undefined || value === null || value === '') return false
    if (key === 'limit' && Number(value) === 100) return false
    if (key === 'offset' && Number(value) === 0) return false
    return true
  })
  return meaningfulEntries.length === 0
}

function wrapMutation<TArgs extends unknown[], TResult>(
  original: (...args: TArgs) => Promise<TResult>,
  invalidations: string[],
) {
  return async (...args: TArgs) => {
    const result = await original(...args)
    for (const prefix of invalidations) invalidatePersistentCache(prefix)
    return result
  }
}

/**
 * Installs lightweight persistent stale-while-revalidate wrappers around
 * page-load API calls. This deliberately avoids playback, queue, lobby state,
 * search, and other realtime/high-churn endpoints.
 */
export function installPersistentApiCache() {
  if (installed || typeof window === 'undefined') return
  installed = true

  const originalHomeSummary = api.homeSummary.bind(api)
  api.homeSummary = (() =>
    persistentSWR('home:summary', originalHomeSummary)
  ) as typeof api.homeSummary

  const originalCapabilities = api.capabilities.bind(api)
  api.capabilities = (() =>
    persistentSWR('capabilities', originalCapabilities)
  ) as typeof api.capabilities

  const originalUserSettings = api.userSettings.bind(api)
  api.userSettings = (() =>
    persistentSWR('user-settings', originalUserSettings)
  ) as typeof api.userSettings

  const originalStations = api.stations.bind(api)
  api.stations = (() =>
    persistentSWR('stations:list', originalStations)
  ) as typeof api.stations

  const originalStationTypes = api.stationTypes.bind(api)
  api.stationTypes = (() =>
    persistentSWR('stations:types', originalStationTypes)
  ) as typeof api.stationTypes

  const originalPlaylists = api.playlists.bind(api)
  api.playlists = (() =>
    persistentSWR('playlists:list', originalPlaylists)
  ) as typeof api.playlists

  const originalPlaylist = api.playlist.bind(api)
  api.playlist = ((id: string) =>
    persistentSWR(`playlist:detail:${id}`, () => originalPlaylist(id))
  ) as typeof api.playlist

  const originalHistory = api.history.bind(api)
  api.history = ((filters: PlaybackHistoryFilters = {}) => {
    if (!isDefaultHistoryRequest(filters)) return originalHistory(filters)
    return persistentSWR('history:recent', () => originalHistory(filters))
  }) as typeof api.history

  // Writes invalidate the relevant saved response. The page's existing load()
  // calls then get authoritative fresh data instead of briefly replaying stale
  // data after a mutation.
  api.createStation = wrapMutation(api.createStation.bind(api), ['stations:list']) as typeof api.createStation
  api.updateStation = wrapMutation(api.updateStation.bind(api), ['stations:list']) as typeof api.updateStation
  api.deleteStation = wrapMutation(api.deleteStation.bind(api), ['stations:list']) as typeof api.deleteStation
  api.uploadStationCover = wrapMutation(api.uploadStationCover.bind(api), ['stations:list']) as typeof api.uploadStationCover
  api.deleteStationCover = wrapMutation(api.deleteStationCover.bind(api), ['stations:list']) as typeof api.deleteStationCover
  api.reloadStationTypes = wrapMutation(api.reloadStationTypes.bind(api), ['stations:types']) as typeof api.reloadStationTypes

  api.createPlaylist = wrapMutation(api.createPlaylist.bind(api), ['playlists:list']) as typeof api.createPlaylist
  api.deletePlaylist = wrapMutation(api.deletePlaylist.bind(api), ['playlists:list', 'playlist:detail:']) as typeof api.deletePlaylist
  api.addSongToPlaylist = wrapMutation(api.addSongToPlaylist.bind(api), ['playlists:list', 'playlist:detail:']) as typeof api.addSongToPlaylist
  api.removePlaylistTrack = wrapMutation(api.removePlaylistTrack.bind(api), ['playlists:list', 'playlist:detail:']) as typeof api.removePlaylistTrack
  api.reorderPlaylistTracks = wrapMutation(api.reorderPlaylistTracks.bind(api), ['playlist:detail:']) as typeof api.reorderPlaylistTracks
  api.applyPlaylistImport = wrapMutation(api.applyPlaylistImport.bind(api), ['playlists:list', 'playlist:detail:']) as typeof api.applyPlaylistImport

  api.setHistoryLimit = wrapMutation(api.setHistoryLimit.bind(api), ['history:recent']) as typeof api.setHistoryLimit
  api.updateUserSettings = wrapMutation(api.updateUserSettings.bind(api), ['user-settings']) as typeof api.updateUserSettings
  api.resetUserSettings = wrapMutation(api.resetUserSettings.bind(api), ['user-settings']) as typeof api.resetUserSettings

  // Do not carry one account's page cache into another account in the same tab.
  const originalLogin = api.login.bind(api)
  api.login = (async (...args: Parameters<typeof api.login>) => {
    clearPersistentCache()
    return originalLogin(...args)
  }) as typeof api.login

  const originalSetup = api.setup.bind(api)
  api.setup = (async (...args: Parameters<typeof api.setup>) => {
    clearPersistentCache()
    return originalSetup(...args)
  }) as typeof api.setup

  const originalLogout = api.logout.bind(api)
  api.logout = (async (...args: Parameters<typeof api.logout>) => {
    try {
      return await originalLogout(...args)
    } finally {
      clearPersistentCache()
    }
  }) as typeof api.logout
}

export function subscribePersistentCache(
  prefixes: string[],
  onUpdate: (key: string) => void,
) {
  if (typeof window === 'undefined') return () => {}

  const handler = (event: Event) => {
    const detail = (event as CustomEvent<CacheUpdateDetail>).detail
    const key = detail?.key || ''
    if (!key || !prefixes.some((prefix) => key.startsWith(prefix))) return
    onUpdate(key)
  }

  window.addEventListener(CACHE_EVENT, handler)
  return () => window.removeEventListener(CACHE_EVENT, handler)
}
