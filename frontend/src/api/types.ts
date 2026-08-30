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
  thumbnail?: string
  thumbnail_url?: string
  thumbnails?: Array<{ url?: string; width?: number; height?: number }>
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
  subsonic_available?: boolean
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

export type SearchArtist = {
  kind?: 'artist' | string
  browse_id: string
  artist_id?: string
  name: string
  thumbnail_url?: string
  art_url?: string
  subscriber_count?: string
  monthly_listeners?: string
  ytmusic_url?: string
}

export type ArtistDetail = SearchArtist & {
  description?: string
  description_source?: 'wikipedia' | 'ytmusic' | string
  description_source_url?: string
  wikipedia_title?: string
  wikipedia_url?: string
  views?: string
  songs_count?: number
  albums_count?: number
  singles_count?: number
  top_tracks_hint?: string[]
  top_albums_hint?: string[]
  mb_artist_id?: string
  mb_resolution_status?: string
}

export type ArtistPopularResponse = {
  artist_name?: string
  yt_browse_id?: string
  tracks: SearchSong[]
}


export type ArtistSimilarResponse = {
  artist_name?: string
  yt_browse_id?: string
  similar_artists: SearchArtist[]
}

export type ArtistAlbumsResponse = {
  artist_name?: string
  yt_browse_id?: string
  albums: SearchAlbum[]
  singles: SearchAlbum[]
}

export type AlbumDetail = {
  browse_id: string
  title: string
  artist: string
  year?: string | number
  thumbnail_url?: string
  art_url?: string
  tracks: SearchSong[]
  subsonic_complete?: boolean
  subsonic_album_id?: string | null
}

export type PlaybackHistoryItem = QueueItem & {
  queue_item_id?: string
  station_id?: string
  event?: string
  reason?: string
  played_ms?: number
  created_at: string
}

export type PlaybackHistoryResponse = {
  limit: number
  offset: number
  total: number
  has_more: boolean
  items: PlaybackHistoryItem[]
}

export type PlaybackHistoryFilters = {
  q?: string
  artist?: string
  album?: string
  source?: string
  event?: string
  station_id?: string
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}

export type StationConfigOption = {
  key: string
  label: string
  type: 'string' | 'number' | 'integer' | 'boolean' | 'select' | 'multiselect' | 'textarea' | string
  description?: string
  required?: boolean
  default?: unknown
  min?: number
  max?: number
  step?: number
  choices?: Array<{ label?: string; value: unknown }>
  min_items?: number
  max_items?: number
  category?: string
  category_label?: string
  category_order?: number
  order?: number
}

export type StationProviderInfo = {
  station_type: string
  display_name: string
  description: string
  version?: string
  builtin?: boolean
  config_options: StationConfigOption[]
}

export type Station = {
  id: string
  name: string
  station_type?: string
  config?: Record<string, unknown>
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
  thumbnail_url?: string
  has_custom_cover?: boolean
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

export type PlaylistTrack = QueueItem & {
  key?: string
  created_at?: string
}

export type PlaylistDetail = {
  playlist: Playlist
  tracks: PlaylistTrack[]
}

export type User = {
  id: string
  username: string
  role: 'admin' | 'user' | string
  is_admin?: boolean
}

export type UserSettings = {
  appearance_accent_color: string
  appearance_accent_contrast_color: string
  appearance_logo_follow_accent: boolean
  appearance_logo_color: string
  appearance_background_color: string
  appearance_surface_color: string
  appearance_surface_soft_color: string
  appearance_surface_raised_color: string
  appearance_sidebar_color: string
  appearance_queue_color: string
  appearance_player_color: string
  appearance_control_color: string
  appearance_text_color: string
  appearance_muted_color: string
  appearance_faint_color: string
  appearance_border_color: string
  appearance_danger_color: string
  appearance_success_color: string
  appearance_reduce_motion: boolean
  appearance_artwork_backgrounds: boolean
  appearance_ui_density: 'compact' | 'comfortable' | 'spacious'
  appearance_artwork_radius: 'square' | 'soft' | 'rounded'
  queue_add_position: 'append' | 'next'
  queue_show_duration: boolean
  queue_show_playing_indicator: boolean
  playback_default_volume: number
  search_default_mode: 'hybrid' | 'subsonic' | 'ytmusic'
  search_default_tab: 'songs' | 'albums' | 'artists'
  station_queue_ahead: number
  lobbies_default_name: string
  lobbies_default_guests_can_add: boolean
  lobbies_auto_copy_invite: boolean
  notifications_import_queued: boolean
  notifications_duration: 'short' | 'normal' | 'long'
  advanced_custom_css: string
}

export type UserSettingsPayload = {
  settings: UserSettings
  limits: { station_queue_ahead_max: number }
}

export type LikeState = {
  liked: boolean
}

export type DislikeState = {
  disliked: boolean
}

export type AudioIntent = {
  id: number
  action: 'play' | 'pause'
}


export type LobbyPermissions = {
  can_add_to_queue: boolean
  can_remove_own_queue_items: boolean
  can_remove_any_queue_item: boolean
  can_control_playback: boolean
  can_skip: boolean
  can_seek: boolean
}

export type LobbyMember = {
  id: string
  nickname: string
  role: 'host' | 'guest' | string
  is_active: boolean
  permissions: LobbyPermissions
  joined_at: string
  last_seen_at: string
}

export type LobbyQueueItem = {
  id: string
  position: number
  title: string
  artist: string
  album?: string
  duration_ms?: number
  art_url?: string
  source?: string
  subsonic_song_id?: string
  yt_video_id?: string
  yt_browse_id?: string
  mb_recording_id?: string
  mb_artist_id?: string
  station_id?: string
  station_name?: string
  added_by_member_id?: string
  added_by_nickname?: string
  created_at: string
}

export type LobbyHistoryItem = {
  id: string
  queue_item_id?: string
  title: string
  artist: string
  album?: string
  duration_ms?: number
  art_url?: string
  source?: string
  subsonic_song_id?: string
  yt_video_id?: string
  added_by_member_id?: string
  added_by_nickname?: string
  played_at: string
}

export type LobbyState = {
  id: string
  name: string
  host_user_id: string
  invite_code?: string | null
  has_password: boolean
  is_open: boolean
  guest_permissions: LobbyPermissions
  guest_queue_limit: number
  cleanup_after_days: number
  active_station_id: string
  active_station_name: string
  self_member_id: string
  self_role: 'host' | 'guest' | string
  self_permissions: LobbyPermissions
  is_playing: boolean
  current_index: number
  position_ms: number
  effective_position_ms: number
  server_time_ms: number
  position_updated_at: string
  now_playing?: LobbyQueueItem | null
  queue: LobbyQueueItem[]
  members: LobbyMember[]
  history: LobbyHistoryItem[]
  created_at: string
  updated_at: string
}

export type LobbyJoinResponse = {
  guest_token: string
  member: LobbyMember
  lobby: LobbyState
}

export type LobbyListResponse = {
  lobbies: LobbyState[]
}


export type HomeAttentionItem = {
  id: string
  severity: 'info' | 'warning' | 'error' | string
  title: string
  detail: string
  href?: string
}

export type HomeActivityItem = {
  id: string
  kind: string
  title: string
  detail: string
  icon?: string
  art_url?: string
  source?: string
  created_at: string
}

export type HomeSummary = {
  generated_at: string
  health: {
    status: 'ok' | 'attention' | string
    label: string
  }
  attention: HomeAttentionItem[]
  recent_activity: HomeActivityItem[]
}


export type AdminUser = {
  id: string
  username: string
  role: 'admin' | 'user' | string
  is_active: boolean
  subsonic_import_override: boolean
  can_import_subsonic: boolean
}


export type Capabilities = {
  subsonic_configured: boolean
  features: {
    library_search: boolean
    subsonic_import: boolean
    library_only_stations: boolean
    subsonic_playback: boolean
    ytmusic_discovery: boolean
    ytmusic_playback: boolean
    lobbies: boolean
    [key: string]: boolean
  }
}

export type PlaylistImportSource = 'helix' | 'ytmusic' | 'spotify' | 'pandora'

export type PlaylistImportCandidate = {
  title: string
  artist: string
  album?: string
  duration_ms?: number
  art_url?: string
  source?: string
  subsonic_song_id?: string
  yt_video_id?: string
  yt_browse_id?: string
  confidence?: number
}

export type PlaylistImportTrack = {
  index: number
  source_track: {
    source: string
    source_track_id: string
    title: string
    artist: string
    album?: string
    duration_ms?: number
    artwork_url?: string
    isrc?: string
    yt_video_id?: string
  }
  status: 'matched' | 'review' | 'unmatched' | 'duplicate'
  confidence: number
  candidate: PlaylistImportCandidate | null
  alternatives: PlaylistImportCandidate[]
}

export type PlaylistImportPreview = {
  source: PlaylistImportSource
  playlist_name: string
  reported_count?: number | null
  parsed_count: number
  counts: Record<'matched' | 'review' | 'unmatched' | 'duplicate', number>
  tracks: PlaylistImportTrack[]
}
