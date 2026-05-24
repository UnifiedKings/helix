export type QueueItem = {
  id: string
  position: number
  title: string
  artist: string
  album?: string
  duration_ms?: number
  seekable_ms?: number
  available_bytes?: number
  is_final?: boolean
  art_url?: string
  source?: string
  subsonic_song_id?: string
  yt_video_id?: string
  yt_browse_id?: string
  mb_recording_id?: string
  mb_artist_id?: string
  is_playable?: boolean
  error?: string
}

export type PlayerState = {
  is_playing: boolean
  current_index: number
  now_playing: QueueItem | null
  queue: QueueItem[]
  autoplay_enabled: boolean
  active_station_id: string
  active_station?: Station | null
}

export type SearchSong = {
  title: string
  artist: string
  album?: string
  duration_ms?: number
  duration_seconds?: number
  art_url?: string
  thumbnail?: string
  thumbnail_url?: string
  thumbnails?: Array<{ url?: string; width?: number; height?: number }>
  source?: string
  subsonic_song_id?: string
  videoId?: string
  video_id?: string
  yt_video_id?: string
  ytmusic_url?: string
}

export type SearchAlbum = {
  title: string
  artist?: string
  year?: string | number
  art_url?: string
  thumbnail?: string
  thumbnail_url?: string
  thumbnails?: Array<{ url?: string; width?: number; height?: number }>
  browseId?: string
  browse_id?: string
  yt_browse_id?: string
  source?: string
  subsonic_album_id?: string
}

export type SearchMode = 'hybrid' | 'subsonic' | 'ytmusic'

export type SearchResponse = {
  mode?: SearchMode
  songs: SearchSong[]
  albums: SearchAlbum[]
}

export type Station = {
  id: string
  name: string
  seed_type: 'artist' | 'track' | string
  seed_title?: string
  seed_artist?: string
  mb_artist_id?: string
  mb_recording_id?: string
  discovery?: number
  seed_influence?: number
  artist_cooldown?: number
  artist_variety?: number
  allow_seed_alternates?: boolean
  era_start?: number
  era_end?: number
  popularity_bias?: number
  tag_strictness?: number
  popular_track_pool_size?: number
  artist_blacklist?: string
  temperature?: number
  cover_url?: string
  created_at?: string
  updated_at?: string
}

export type Playlist = {
  id: string
  name: string
  system_key?: string
  track_count?: number
  cover_url?: string
  thumbnail_url?: string
  created_at?: string
}

export type PlaylistDetail = {
  playlist: Playlist
  tracks: QueueItem[]
}

export type User = {
  id: string
  username: string
  is_admin?: boolean
}

export type AudioIntent = {
  id: number
  action: 'play' | 'pause'
}
