import type { AlbumDetail, ArtistAlbumsResponse, ArtistDetail, ArtistPopularResponse, ArtistSimilarResponse, DislikeState, HomeSummary, LikeState, PlaybackHistoryFilters, PlaybackHistoryResponse, PlayerState, Playlist, PlaylistDetail, QueueItem, SearchAlbum, SearchArtist, SearchMode, SearchResponse, SearchSong, Station, StationProviderInfo, AdminUser, Capabilities, User, UserSettingsPayload, UserSettings, LobbyJoinResponse, LobbyListResponse, LobbyPermissions, LobbyState, PlaylistImportPreview, PlaylistImportSource, PlaylistImportCandidate } from './types'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { headers, ...rest } = options
  const res = await fetch(path, {
    credentials: 'include',
    ...rest,
    headers: { 'Content-Type': 'application/json', ...(headers ?? {}) },
  })

  const responseText = await res.text()

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    if (responseText) {
      try {
        const body = JSON.parse(responseText)
        detail = body.detail ?? JSON.stringify(body)
      } catch {
        detail = responseText
      }
    }
    throw new Error(detail || 'Request failed')
  }

  if (res.status === 204 || !responseText) return undefined as T
  return JSON.parse(responseText) as T
}

function announceSubsonicImportQueued(kind: 'track' | 'album', title?: string) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('helix:subsonic-import-queued', {
    detail: { kind, title },
  }))
}


async function rawRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    ...options,
  })

  const responseText = await res.text()

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    if (responseText) {
      try {
        const body = JSON.parse(responseText)
        detail = body.detail ?? JSON.stringify(body)
      } catch {
        detail = responseText
      }
    }
    throw new Error(detail || 'Request failed')
  }

  if (res.status === 204 || !responseText) return undefined as T
  return JSON.parse(responseText) as T
}


function bestArtworkUrl(item: { art_url?: string; thumbnail?: string; thumbnail_url?: string; thumbnails?: Array<{ url?: string; width?: number; height?: number }> }): string {
  if (item.art_url) return item.art_url
  if (item.thumbnail_url) return item.thumbnail_url
  if (item.thumbnail) return item.thumbnail
  const thumbs = Array.isArray(item.thumbnails) ? [...item.thumbnails] : []
  thumbs.sort((a, b) => ((b.width ?? 0) * (b.height ?? 0)) - ((a.width ?? 0) * (a.height ?? 0)))
  return thumbs.find((thumb) => thumb.url)?.url ?? ''
}

function normalizeSong(song: SearchSong): SearchSong {
  return { ...song, art_url: bestArtworkUrl(song) }
}

function normalizeAlbum(album: SearchAlbum): SearchAlbum {
  return { ...album, art_url: bestArtworkUrl(album) }
}

function normalizeSearchResponse(payload: SearchResponse): SearchResponse {
  return {
    mode: payload.mode,
    songs: (payload.songs ?? []).map(normalizeSong),
    albums: (payload.albums ?? []).map(normalizeAlbum),
  }
}

function normalizeArtist(artist: SearchArtist): SearchArtist {
  return { ...artist, art_url: artist.art_url || artist.thumbnail_url || '' }
}

function normalizeAlbumDetail(album: AlbumDetail): AlbumDetail {
  const artUrl = album.art_url || album.thumbnail_url || ''
  return {
    ...album,
    art_url: artUrl,
    tracks: (album.tracks ?? []).map((track) => normalizeSong({
      ...track,
      album: track.album || album.title,
      artist: track.artist || album.artist,
      art_url: track.art_url || artUrl,
      thumbnail_url: track.thumbnail_url || artUrl,
      yt_video_id: track.yt_video_id || track.video_id || track.videoId || '',
      source: track.source || 'ytmusic',
    })),
  }
}

function normalizePlaylist(playlist: Playlist): Playlist {
  return {
    ...playlist,
    cover_url: playlist.cover_url || playlist.thumbnail_url || '',
  }
}

function normalizeQueueItem<T extends QueueItem>(item: T): T {
  return {
    ...item,
    art_url: bestArtworkUrl(item) || item.art_url || '',
  }
}

function normalizePlaylistDetail(detail: PlaylistDetail): PlaylistDetail {
  return {
    ...detail,
    playlist: normalizePlaylist(detail.playlist),
    tracks: (detail.tracks ?? []).map(normalizeQueueItem),
  }
}

function songToPayload(song: SearchSong) {
  const ytVideoId = song.yt_video_id || song.video_id || song.videoId || ''
  const subsonicSongId = song.subsonic_song_id || ''
  return {
    title: song.title,
    artist: song.artist,
    album: song.album ?? '',
    duration_ms: song.duration_ms ?? (song.duration_seconds ? song.duration_seconds * 1000 : 0),
    art_url: bestArtworkUrl(song),
    source: song.source ?? (subsonicSongId ? 'subsonic' : ytVideoId ? 'ytmusic' : ''),
    subsonic_song_id: subsonicSongId,
    yt_video_id: ytVideoId,
    ytmusic_url: song.ytmusic_url || (ytVideoId ? `https://music.youtube.com/watch?v=${ytVideoId}` : ''),
  }
}

function albumToPayload(album: SearchAlbum) {
  return {
    browse_id: album.yt_browse_id || album.browse_id || album.browseId || '',
    title: album.title,
    artist: album.artist ?? '',
    art_url: bestArtworkUrl(album),
  }
}

function queueItemToRatingPayload(item: QueueItem) {
  return {
    title: item.title,
    artist: item.artist,
    album: item.album ?? '',
    duration_ms: item.duration_ms ?? 0,
    art_url: item.art_url ?? '',
    source: item.source ?? '',
    subsonic_song_id: item.subsonic_song_id ?? '',
    yt_video_id: item.yt_video_id ?? '',
    yt_browse_id: item.yt_browse_id ?? '',
    mb_recording_id: item.mb_recording_id ?? '',
    mb_artist_id: item.mb_artist_id ?? '',
  }
}

function identityQuery(item: QueueItem) {
  const params = new URLSearchParams()
  if (item.subsonic_song_id) params.set('subsonic_song_id', item.subsonic_song_id)
  if (item.yt_video_id) params.set('yt_video_id', item.yt_video_id)
  return params.toString()
}



function websocketUrl(path: string) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

function lobbyTokenKey(lobbyId: string) {
  return `helix.lobby.token.${lobbyId}`
}

function lobbyInviteKey(inviteCode: string) {
  return `helix.lobby.invite.${inviteCode}`
}

function getLobbyToken(lobbyId: string) {
  return window.localStorage.getItem(lobbyTokenKey(lobbyId)) || ''
}

function saveLobbyToken(lobbyId: string, token: string) {
  if (lobbyId && token) window.localStorage.setItem(lobbyTokenKey(lobbyId), token)
}

function saveLobbyInviteMapping(inviteCode: string, lobbyId: string, token: string, nickname?: string) {
  const invite = inviteCode.trim()
  if (!invite || !lobbyId || !token) return
  window.localStorage.setItem(lobbyInviteKey(invite), JSON.stringify({ lobbyId, token, nickname: nickname || '', saved_at: Date.now() }))
  saveLobbyToken(lobbyId, token)
}

function readLobbyInviteMapping(inviteCode: string): { lobbyId: string; token: string; nickname?: string } | null {
  const raw = window.localStorage.getItem(lobbyInviteKey(inviteCode.trim()))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as { lobbyId?: string; token?: string; nickname?: string }
    if (!parsed.lobbyId || !parsed.token) return null
    return { lobbyId: parsed.lobbyId, token: parsed.token, nickname: parsed.nickname || '' }
  } catch {
    return null
  }
}

function clearLobbyInviteMapping(inviteCode: string) {
  const invite = inviteCode.trim()
  if (!invite) return
  const mapping = readLobbyInviteMapping(invite)
  if (mapping?.lobbyId) window.localStorage.removeItem(lobbyTokenKey(mapping.lobbyId))
  window.localStorage.removeItem(lobbyInviteKey(invite))
}

function lobbyHeaders(lobbyId: string): Record<string, string> {
  const token = getLobbyToken(lobbyId)
  return token ? { 'x-helix-lobby-token': token } : {}
}

function lobbyTokenHeaders(token: string): Record<string, string> {
  return token ? { 'x-helix-lobby-token': token } : {}
}

function lobbyQueuePayload(song: SearchSong | QueueItem | { title: string; artist: string; album?: string }) {
  if ('duration_ms' in song || 'duration_seconds' in song || 'yt_video_id' in song || 'video_id' in song || 'videoId' in song || 'subsonic_song_id' in song || 'source' in song) {
    return songToPayload(song as SearchSong)
  }
  return {
    title: song.title,
    artist: song.artist,
    album: song.album ?? '',
    duration_ms: 0,
    art_url: '',
    source: '',
    subsonic_song_id: '',
    yt_video_id: '',
    ytmusic_url: '',
  }
}

export const api = {
  me: () => request<User>('/auth/me'),
  login: (username: string, password: string) => request<User>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  setup: (username: string, password: string) => request<User>('/setup', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  setupEnabled: () => request<{ enabled: boolean }>('/setup/enabled'),

  health: () => request<{ ok?: boolean; status?: string }>('/health'),
  homeSummary: () => request<HomeSummary>('/api/home/summary'),
  settings: () => request<Record<string, unknown>>('/api/settings'),
  userSettings: () => request<UserSettingsPayload>('/api/user/settings'),
  updateUserSettings: (payload: Partial<UserSettings>) => request<UserSettingsPayload>('/api/user/settings', { method: 'PATCH', body: JSON.stringify(payload) }),
  resetUserSettings: () => request<UserSettingsPayload>('/api/user/settings', { method: 'DELETE' }),

  playerState: () => request<PlayerState>(`/api/playback/state?t=${Date.now()}`, { cache: 'no-store' }),
  playerSocketUrl: () => websocketUrl('/ws/player'),
  playSong: (song: SearchSong) => request<PlayerState>('/api/playback/track', { method: 'POST', body: JSON.stringify(songToPayload(song)) }),
  playAlbum: (album: SearchAlbum) => request<PlayerState>('/api/playback/album', { method: 'POST', body: JSON.stringify(albumToPayload(album)) }),
  playPlaylist: (playlistId: string, shuffle = false) => request<PlayerState>('/api/playback/playlist', { method: 'POST', body: JSON.stringify({ playlist_id: playlistId, shuffle }) }),
  pause: () => request<PlayerState>('/api/playback/pause', { method: 'POST', body: JSON.stringify({}) }),
  resume: () => request<PlayerState>('/api/playback/resume', { method: 'POST', body: JSON.stringify({}) }),
  next: () => request<PlayerState>('/api/playback/next', { method: 'POST', body: JSON.stringify({}) }),
  previous: () => request<PlayerState>('/api/playback/previous', { method: 'POST', body: JSON.stringify({}) }),
  ended: (repeatQueue = false) => request<PlayerState>('/api/playback/ended', {
    method: 'POST',
    body: JSON.stringify({ repeat_queue: repeatQueue }),
  }),
  jump: (index: number) => request<PlayerState>('/api/playback/jump', { method: 'POST', body: JSON.stringify({ index }) }),
  setAutoplay: (enabled: boolean) => request<PlayerState>('/api/playback/autoplay', { method: 'POST', body: JSON.stringify({ enabled }) }),

  isLiked: (item: QueueItem) => request<LikeState>(`/api/likes/is-liked?${identityQuery(item)}`),
  toggleLike: (item: QueueItem) => request<LikeState>('/api/likes/toggle', { method: 'POST', body: JSON.stringify(queueItemToRatingPayload(item)) }),
  isDisliked: (item: QueueItem) => request<DislikeState>(`/api/dislikes/is-disliked?${identityQuery(item)}`),
  toggleDislike: (item: QueueItem) => request<DislikeState>('/api/dislikes/toggle', { method: 'POST', body: JSON.stringify(queueItemToRatingPayload(item)) }),

  queueSong: (song: SearchSong) => request<PlayerState>('/api/queue/track', { method: 'POST', body: JSON.stringify(songToPayload(song)) }),
  queueAlbum: (album: SearchAlbum) => request<PlayerState>('/api/queue/album', { method: 'POST', body: JSON.stringify(albumToPayload(album)) }),
  removeQueueItem: (id: string) => request<{ ok: boolean }>(`/api/queue/items/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  clearQueue: () => request<PlayerState>('/api/queue/items/clear', { method: 'DELETE' }),
  reorderQueue: (itemIds: string[]) => request<PlayerState>('/api/queue/items/reorder', { method: 'PATCH', body: JSON.stringify({ item_ids: itemIds }) }),

  search: async (q: string, mode: SearchMode = 'hybrid') => normalizeSearchResponse(await request<SearchResponse>(`/api/search/${mode}?q=${encodeURIComponent(q)}&song_limit=20&album_limit=20`)),
  lobbySearch: async (lobbyId: string, q: string, mode: SearchMode = 'hybrid') => normalizeSearchResponse(await request<SearchResponse>(`/api/lobbies/${encodeURIComponent(lobbyId)}/search/${mode}?q=${encodeURIComponent(q)}&song_limit=20&album_limit=20`, { headers: lobbyHeaders(lobbyId) })),
  searchArtists: async (q: string) => {
    const payload = await request<{ artists: SearchArtist[] }>(`/api/ytmusic/search/artists?q=${encodeURIComponent(q)}&artist_limit=12`)
    return { artists: (payload.artists ?? []).map(normalizeArtist) }
  },

  addSongToSubsonic: (song: SearchSong | QueueItem) => {
    announceSubsonicImportQueued('track', song.title)
    return request<{ ok: boolean; video_id?: string }>('/api/subsonic/add/track', { method: 'POST', body: JSON.stringify(songToPayload(song as SearchSong)) })
  },
  resolveSubsonicSongs: (songs: Array<{ key: string; title: string; artist: string; album?: string; duration_ms?: number; yt_video_id?: string }>) => request<{ songs: Record<string, { available: boolean; subsonic_song_id?: string | null }> }>('/api/subsonic/resolve', { method: 'POST', body: JSON.stringify({ songs, albums: [] }) }),
  addAlbumToSubsonic: (album: SearchAlbum | AlbumDetail) => {
    announceSubsonicImportQueued('album', album.title)
    return request<{ ok: boolean; total?: number; enqueued?: number; skipped_existing?: number }>('/api/subsonic/add/album', { method: 'POST', body: JSON.stringify(albumToPayload(album as SearchAlbum)) })
  },

  artist: async (browseId: string) => normalizeArtist(await request<ArtistDetail>(`/api/ytmusic/artists/${encodeURIComponent(browseId)}`)) as ArtistDetail,
  artistPopular: async (browseId: string) => {
    const payload = await request<ArtistPopularResponse>(`/api/ytmusic/artists/${encodeURIComponent(browseId)}/popular?limit=20`)
    return { ...payload, tracks: (payload.tracks ?? []).map((track) => normalizeSong({ ...track, source: track.source || 'ytmusic' })) }
  },
  artistAlbums: async (browseId: string) => {
    const payload = await request<ArtistAlbumsResponse>(`/api/ytmusic/artists/${encodeURIComponent(browseId)}/albums?limit=50`)
    return {
      ...payload,
      albums: (payload.albums ?? []).map((album) => normalizeAlbum({ ...album, source: album.source || 'ytmusic' })),
      singles: (payload.singles ?? []).map((album) => normalizeAlbum({ ...album, source: album.source || 'ytmusic' })),
    }
  },
  artistSimilar: async (browseId: string) => {
    const payload = await request<ArtistSimilarResponse>(`/api/ytmusic/artists/${encodeURIComponent(browseId)}/similar?limit=12`)
    return {
      ...payload,
      similar_artists: (payload.similar_artists ?? []).map((artist) => normalizeArtist(artist)),
    }
  },
  album: async (albumId: string, source?: string) => normalizeAlbumDetail(await request<AlbumDetail>(`/api/album/${encodeURIComponent(albumId)}${source ? `?source=${encodeURIComponent(source)}` : ''}`)),

  history: (filters: PlaybackHistoryFilters = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value === undefined || value === null || value === '') continue
      params.set(key, String(value))
    }
    const suffix = params.toString() ? `?${params.toString()}` : ''
    return request<PlaybackHistoryResponse>(`/api/history${suffix}`)
  },
  replayHistory: (historyId: string) => request<PlayerState>('/api/playback/replay', { method: 'POST', body: JSON.stringify({ history_id: historyId }) }),
  setHistoryLimit: (limit: number) => request<PlaybackHistoryResponse>('/api/history/limit', { method: 'POST', body: JSON.stringify({ limit }) }),

  capabilities: () => request<Capabilities>('/capabilities'),
  adminSettings: () => request<Record<string, unknown>>('/api/admin/settings'),
  updateAdminSettings: (payload: Record<string, unknown>) => request<Record<string, unknown>>('/api/admin/settings', { method: 'PATCH', body: JSON.stringify(payload) }),
  adminUsers: () => request<AdminUser[]>('/admin/users'),
  createAdminUser: (payload: { username: string; password: string; role: 'admin' | 'user' }) => request<AdminUser>('/admin/users', { method: 'POST', body: JSON.stringify(payload) }),
  updateAdminUser: (id: string, payload: { is_active?: boolean; role?: 'admin' | 'user'; subsonic_import_override?: boolean }) => request<AdminUser>(`/admin/users/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  stationTypes: () => request<StationProviderInfo[]>('/api/stations/types'),
  reloadStationTypes: () => request<StationProviderInfo[]>('/api/stations/types/reload', { method: 'POST', body: JSON.stringify({}) }),
  stations: () => request<Station[]>('/api/stations'),
  createStation: (payload: { name: string; station_type: string; config: Record<string, unknown>; seed_type?: string; seed_artist?: string; seed_title?: string }) => request<Station>('/api/stations', { method: 'POST', body: JSON.stringify(payload) }),
  updateStation: (id: string, payload: { name?: string; station_type?: string; config?: Record<string, unknown> }) => request<Station>(`/api/stations/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  playStation: (id: string) => request<PlayerState>(`/api/stations/${encodeURIComponent(id)}/play`, { method: 'POST', body: JSON.stringify({}) }),
  deleteStation: (id: string) => request<{ ok: boolean }>(`/api/stations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  uploadStationCover: (id: string, file: File) => {
    const headers = file.type ? { 'Content-Type': file.type } : undefined
    return rawRequest<Station>(`/api/stations/${encodeURIComponent(id)}/cover`, { method: 'POST', headers, body: file })
  },
  deleteStationCover: (id: string) => request<Station>(`/api/stations/${encodeURIComponent(id)}/cover`, { method: 'DELETE' }),

  playlists: async () => (await request<Playlist[]>('/api/playlists')).map(normalizePlaylist),
  createPlaylist: (name: string) => request<Playlist>('/api/playlists', { method: 'POST', body: JSON.stringify({ name }) }),
  playlist: async (id: string) => normalizePlaylistDetail(await request<PlaylistDetail>(`/api/playlists/${encodeURIComponent(id)}`)),
  addSongToPlaylist: async (playlistId: string, song: SearchSong) => normalizePlaylistDetail(await request<PlaylistDetail>(`/api/playlists/${encodeURIComponent(playlistId)}/tracks`, { method: 'POST', body: JSON.stringify(songToPayload(song)) })),
  removePlaylistTrack: async (playlistId: string, trackId: string) => normalizePlaylistDetail(await request<PlaylistDetail>(`/api/playlists/${encodeURIComponent(playlistId)}/tracks/${encodeURIComponent(trackId)}`, { method: 'DELETE' })),
  reorderPlaylistTracks: async (playlistId: string, trackIds: string[]) => normalizePlaylistDetail(await request<PlaylistDetail>(`/api/playlists/${encodeURIComponent(playlistId)}/tracks/reorder`, { method: 'PATCH', body: JSON.stringify({ track_ids: trackIds }) })),
  deletePlaylist: (id: string) => request<{ ok: boolean }>(`/api/playlists/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  exportPlaylist: (id: string) => request<Record<string, unknown>>(`/api/playlists/${encodeURIComponent(id)}/export`),
  previewPlaylistImport: (playlistId: string, payload: { source: PlaylistImportSource; url?: string; filename?: string; content?: string }) => request<PlaylistImportPreview>(`/api/playlists/${encodeURIComponent(playlistId)}/import/preview`, { method: 'POST', body: JSON.stringify(payload) }),
  applyPlaylistImport: async (playlistId: string, tracks: PlaylistImportCandidate[], skipExisting = true) => normalizePlaylistDetail(await request<PlaylistDetail>(`/api/playlists/${encodeURIComponent(playlistId)}/import/apply`, { method: 'POST', body: JSON.stringify({ tracks, skip_existing: skipExisting }) })),

  lobbies: () => request<LobbyListResponse>('/api/lobbies'),
  createLobby: (name: string, guestPermissions?: LobbyPermissions, guestQueueLimit?: number, password?: string) => request<LobbyState>('/api/lobbies', { method: 'POST', body: JSON.stringify({ name, guest_permissions: guestPermissions, guest_queue_limit: guestQueueLimit, password: password?.trim() || null }) }),
  updateLobby: (lobbyId: string, payload: { name?: string; password?: string | null; is_open?: boolean; guest_permissions?: LobbyPermissions; guest_queue_limit?: number; cleanup_after_days?: number }) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  regenerateLobbyInvite: (lobbyId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/invite/regenerate`, { method: 'POST', body: JSON.stringify({}) }),
  playLobbyStation: (lobbyId: string, stationId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/station/${encodeURIComponent(stationId)}/play`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({}) }),
  stopLobbyStation: (lobbyId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/station/stop`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({}) }),
  deleteLobby: (lobbyId: string) => request<{ ok: boolean }>(`/api/lobbies/${encodeURIComponent(lobbyId)}`, { method: 'DELETE' }),
  joinLobby: async (inviteCode: string, nickname: string, password?: string) => {
    const cleanedInvite = inviteCode.trim().toUpperCase()
    const cleanedNickname = nickname.trim()
    const response = await request<LobbyJoinResponse>('/api/lobbies/join', { method: 'POST', body: JSON.stringify({ invite_code: cleanedInvite, nickname: cleanedNickname, password: password || null }) })
    saveLobbyInviteMapping(cleanedInvite, response.lobby.id, response.guest_token, response.member?.nickname || cleanedNickname)
    return response
  },
  savedLobbyInvite: (inviteCode: string) => readLobbyInviteMapping(inviteCode.trim().toUpperCase()),
  clearSavedLobbyInvite: (inviteCode: string) => clearLobbyInviteMapping(inviteCode.trim().toUpperCase()),
  resumeJoinedLobby: (inviteCode: string) => {
    const cleanedInvite = inviteCode.trim().toUpperCase()
    const mapping = readLobbyInviteMapping(cleanedInvite)
    if (mapping) {
      saveLobbyToken(mapping.lobbyId, mapping.token)
      return request<LobbyState>(`/api/lobbies/${encodeURIComponent(mapping.lobbyId)}/state?t=${Date.now()}`, { headers: lobbyTokenHeaders(mapping.token), cache: 'no-store' })
    }
    return request<LobbyState>(`/api/lobbies/join/${encodeURIComponent(cleanedInvite)}/resume?t=${Date.now()}`, { cache: 'no-store' })
  },
  lobbyState: (lobbyId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/state?t=${Date.now()}`, { headers: lobbyHeaders(lobbyId), cache: 'no-store' }),
  lobbySocketUrl: (lobbyId: string) => {
    const token = getLobbyToken(lobbyId)
    const query = token ? `?token=${encodeURIComponent(token)}` : ''
    return websocketUrl(`/ws/lobbies/${encodeURIComponent(lobbyId)}${query}`)
  },
  lobbyAddQueueItem: (lobbyId: string, item: SearchSong | QueueItem | { title: string; artist: string; album?: string }) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/queue`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify(lobbyQueuePayload(item)) }),
  lobbyAddYoutubeUrl: (lobbyId: string, url: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/queue`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({ ytmusic_url: url }) }),
  lobbyRemoveQueueItem: (lobbyId: string, itemId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/queue/${encodeURIComponent(itemId)}`, { method: 'DELETE', headers: lobbyHeaders(lobbyId) }),
  lobbyJumpToQueueItem: (lobbyId: string, itemId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/queue/${encodeURIComponent(itemId)}/play`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({}) }),
  lobbyReorderQueue: (lobbyId: string, itemIds: string[]) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/queue/reorder`, { method: 'PATCH', headers: lobbyHeaders(lobbyId), body: JSON.stringify({ item_ids: itemIds }) }),
  lobbyClearQueue: (lobbyId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/queue`, { method: 'DELETE', headers: lobbyHeaders(lobbyId) }),
  lobbyPlay: (lobbyId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/play`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({}) }),
  lobbyPause: (lobbyId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/pause`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({}) }),
  lobbySeek: (lobbyId: string, positionMs: number) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/seek`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({ position_ms: positionMs }) }),
  lobbyEnded: (lobbyId: string, itemId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/ended`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({ item_id: itemId }) }),
  lobbyNext: (lobbyId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/next`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({}) }),
  lobbyPrevious: (lobbyId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/previous`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({}) }),
  lobbyUpdateSelf: (lobbyId: string, payload: { nickname?: string }) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/me`, { method: 'PATCH', headers: lobbyHeaders(lobbyId), body: JSON.stringify(payload) }),
  lobbyUpdateMember: (lobbyId: string, memberId: string, payload: { nickname?: string; is_active?: boolean; permissions?: LobbyPermissions }) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/members/${encodeURIComponent(memberId)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  lobbyKickMember: (lobbyId: string, memberId: string) => request<LobbyState>(`/api/lobbies/${encodeURIComponent(lobbyId)}/members/${encodeURIComponent(memberId)}`, { method: 'PATCH', body: JSON.stringify({ is_active: false }) }),
  lobbyLeave: (lobbyId: string) => request<{ ok: boolean }>(`/api/lobbies/${encodeURIComponent(lobbyId)}/leave`, { method: 'POST', headers: lobbyHeaders(lobbyId), body: JSON.stringify({}) }),
}
