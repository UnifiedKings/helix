import type { PlayerState, Playlist, PlaylistDetail, SearchAlbum, SearchMode, SearchResponse, SearchSong, Station, User } from './types'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options,
  })

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      detail = body.detail ?? JSON.stringify(body)
    } catch {
      detail = await res.text()
    }
    throw new Error(detail || 'Request failed')
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
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
    songs: (payload.songs ?? []).map(normalizeSong),
    albums: (payload.albums ?? []).map(normalizeAlbum),
  }
}

function normalizePlaylist(playlist: Playlist): Playlist {
  return {
    ...playlist,
    cover_url: playlist.cover_url || playlist.thumbnail_url || '',
  }
}

function songToPayload(song: SearchSong) {
  const ytVideoId = song.yt_video_id || song.video_id || song.videoId || ''
  return {
    title: song.title,
    artist: song.artist,
    album: song.album ?? '',
    duration_ms: song.duration_ms ?? (song.duration_seconds ? song.duration_seconds * 1000 : 0),
    art_url: bestArtworkUrl(song),
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

export const api = {
  me: () => request<User>('/auth/me'),
  login: (username: string, password: string) => request<User>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  setup: (username: string, password: string) => request<User>('/setup', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  setupEnabled: () => request<{ enabled: boolean }>('/setup/enabled'),

  health: () => request<{ ok?: boolean; status?: string }>('/health'),
  settings: () => request<Record<string, unknown>>('/settings'),

  playerState: () => request<PlayerState>('/api/playback/state'),
  playSong: (song: SearchSong) => request<PlayerState>('/api/playback/track', { method: 'POST', body: JSON.stringify(songToPayload(song)) }),
  playAlbum: (album: SearchAlbum) => request<PlayerState>('/api/playback/album', { method: 'POST', body: JSON.stringify(albumToPayload(album)) }),
  playPlaylist: (playlistId: string) => request<PlayerState>('/api/playback/playlist', { method: 'POST', body: JSON.stringify({ playlist_id: playlistId }) }),
  pause: () => request<PlayerState>('/api/playback/pause', { method: 'POST', body: JSON.stringify({}) }),
  resume: () => request<PlayerState>('/api/playback/resume', { method: 'POST', body: JSON.stringify({}) }),
  next: () => request<PlayerState>('/api/playback/next', { method: 'POST', body: JSON.stringify({}) }),
  previous: () => request<PlayerState>('/api/playback/previous', { method: 'POST', body: JSON.stringify({}) }),
  ended: () => request<PlayerState>('/api/playback/ended', { method: 'POST', body: JSON.stringify({}) }),
  jump: (index: number) => request<PlayerState>('/api/playback/jump', { method: 'POST', body: JSON.stringify({ index }) }),
  setAutoplay: (enabled: boolean) => request<PlayerState>('/api/playback/autoplay', { method: 'POST', body: JSON.stringify({ enabled }) }),

  queueSong: (song: SearchSong) => request<PlayerState>('/api/queue/track', { method: 'POST', body: JSON.stringify(songToPayload(song)) }),
  queueAlbum: (album: SearchAlbum) => request<PlayerState>('/api/queue/album', { method: 'POST', body: JSON.stringify(albumToPayload(album)) }),
  removeQueueItem: (id: string) => request<{ ok: boolean }>(`/api/queue/items/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  search: async (q: string, mode: SearchMode = 'hybrid') => normalizeSearchResponse(await request<SearchResponse>(`/api/search/${mode}?q=${encodeURIComponent(q)}&song_limit=20&album_limit=20`)),

  stations: () => request<Station[]>('/api/stations'),
  createStation: (payload: Partial<Station>) => request<Station>('/api/stations', { method: 'POST', body: JSON.stringify(payload) }),
  playStation: (id: string) => request<PlayerState>(`/api/stations/${encodeURIComponent(id)}/play`, { method: 'POST', body: JSON.stringify({}) }),
  deleteStation: (id: string) => request<{ ok: boolean }>(`/api/stations/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  playlists: async () => (await request<Playlist[]>('/api/playlists')).map(normalizePlaylist),
  createPlaylist: (name: string) => request<Playlist>('/api/playlists', { method: 'POST', body: JSON.stringify({ name }) }),
  playlist: async (id: string) => {
    const detail = await request<PlaylistDetail>(`/api/playlists/${encodeURIComponent(id)}`)
    return { ...detail, playlist: normalizePlaylist(detail.playlist) }
  },
  deletePlaylist: (id: string) => request<{ ok: boolean }>(`/api/playlists/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}
