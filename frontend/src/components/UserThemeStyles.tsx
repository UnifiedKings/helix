import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { UserSettingsPayload } from '../api/types'

function hexToRgb(hex: string) {
  const normalized = hex.replace('#', '')
  if (!/^[0-9a-f]{6}$/i.test(normalized)) return null
  return {
    r: parseInt(normalized.slice(0, 2), 16),
    g: parseInt(normalized.slice(2, 4), 16),
    b: parseInt(normalized.slice(4, 6), 16),
  }
}

function shift(hex: string, amount: number) {
  const rgb = hexToRgb(hex)
  if (!rgb) return hex
  const channel = (value: number) => Math.max(0, Math.min(255, Math.round(value + (amount >= 0 ? (255 - value) * amount : value * amount))))
  return `#${[channel(rgb.r), channel(rgb.g), channel(rgb.b)].map((value) => value.toString(16).padStart(2, '0')).join('')}`
}

function densityCss(density: UserSettingsPayload['settings']['appearance_ui_density']) {
  if (density === 'compact') {
    return `
.side-link { min-height: 40px !important; padding-block: .55rem !important; }
.queue-row-redesign, .queue-panel-redesign .queue-item { min-height: 62px !important; }
.settings-control-row { padding-block: .62rem !important; }
.station-card-body { padding: .72rem .78rem !important; }
.search-song-row, .history-row { min-height: 58px !important; }
`
  }
  if (density === 'spacious') {
    return `
.side-link { min-height: 52px !important; padding-block: .86rem !important; }
.queue-row-redesign, .queue-panel-redesign .queue-item { min-height: 78px !important; }
.settings-control-row { padding-block: 1rem !important; }
.station-card-body { padding: 1rem 1.05rem !important; }
.search-song-row, .history-row { min-height: 72px !important; }
`
  }
  return ''
}

function artworkRadiusCss(style: UserSettingsPayload['settings']['appearance_artwork_radius']) {
  const radius = style === 'square' ? '2px' : style === 'rounded' ? '16px' : '8px'
  return `
.artwork,
.lobby-search-result-art,
.home-session-art img,
.album-detail-artwork img,
.artist-hero .artwork,
.search-top-result .artwork,
.history-row .artwork,
.playlist-track-art,
.playlist-search-result-art {
  border-radius: ${radius} !important;
}
`
}

function themeCss(payload: UserSettingsPayload | null) {
  if (!payload) return ''
  const { settings } = payload
  const accent = settings.appearance_accent_color || '#a95f18'
  const rgb = hexToRgb(accent) ?? { r: 169, g: 95, b: 24 }
  const borderRgb = hexToRgb(settings.appearance_border_color || '#252a31') ?? { r: 37, g: 42, b: 49 }
  const customDisabled = new URLSearchParams(window.location.search).get('safe-ui') === '1'
  const queueDurationCss = settings.queue_show_duration ? '' : '.queue-panel-redesign .queue-duration { display: none !important; }\n'
  const queueIndicatorCss = settings.queue_show_playing_indicator ? '' : '.queue-panel-redesign .queue-playing-bars { visibility: hidden !important; }\n'
  const artworkBackgroundCss = settings.appearance_artwork_backgrounds ? '' : '.home-now-hero::before, .home-now-hero::after, .home-session-backdrop { display: none !important; }\n'
  const reduceMotionCss = settings.appearance_reduce_motion
    ? '*, *::before, *::after { animation-duration: 0.001ms !important; animation-iteration-count: 1 !important; transition-duration: 0.001ms !important; scroll-behavior: auto !important; }\n'
    : ''

  return `:root {
  --accent: ${accent};
  --accent-strong: ${shift(accent, 0.16)};
  --accent-bright: ${shift(accent, 0.30)};
  --accent-soft: rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.13);
  --accent-border: rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.34);
  --accent-shadow: rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.28);
  --accent-contrast: ${settings.appearance_accent_contrast_color || '#fff8ef'};
  --logo-color: ${settings.appearance_logo_follow_accent ? accent : (settings.appearance_logo_color || '#d66f12')};

  --bg: ${settings.appearance_background_color || '#080a0d'};
  --surface: ${settings.appearance_surface_color || '#0d1014'};
  --surface-soft: ${settings.appearance_surface_soft_color || '#12161b'};
  --surface-raised: ${settings.appearance_surface_raised_color || '#171b20'};
  --sidebar-bg: ${settings.appearance_sidebar_color || '#0a0d10'};
  --queue-bg: ${settings.appearance_queue_color || '#0d1013'};
  --player-bg: ${settings.appearance_player_color || '#0b0d10'};
  --control-bg: ${settings.appearance_control_color || '#10141a'};
  --text: ${settings.appearance_text_color || '#f5f2ec'};
  --muted: ${settings.appearance_muted_color || '#aaa9a5'};
  --faint: ${settings.appearance_faint_color || '#747570'};
  --border: ${settings.appearance_border_color || '#252a31'};
  --border-soft: rgba(${borderRgb.r}, ${borderRgb.g}, ${borderRgb.b}, 0.58);
  --danger: ${settings.appearance_danger_color || '#ff647d'};
  --good: ${settings.appearance_success_color || '#35e09b'};
}

html,
body,
#root {
  background-color: var(--bg);
  color: var(--text);
}

/* Palette compatibility layer.
   A lot of Helix predates user theming and still has literal dark/amber colors.
   Keep these overrides here, after the bundled styles, so the user's palette
   controls the global shell even before every page stylesheet is fully tokenized. */
.app-shell,
.app-shell-with-sidebar,
.dashboard-grid,
.dashboard-content-card,
main {
  color: var(--text);
}

.app-sidebar {
  background: var(--sidebar-bg) !important;
  border-color: var(--border-soft) !important;
}

.queue-panel-redesign {
  background: var(--queue-bg) !important;
  border-color: var(--border-soft) !important;
}

.playback-bar {
  background: var(--player-bg) !important;
  border-color: var(--border-soft) !important;
}

input,
select,
textarea,
button.secondary,
.settings-segmented,
.settings-segmented button,
.search-input-shell,
.search-bar,
.album-overflow-menu,
.station-card-menu-popover {
  background-color: var(--control-bg);
  color: var(--text);
  border-color: var(--border);
}

.panel,
.settings-card,
.lobby-control-card,
.album-detail-tracks,
.search-top-result,
.history-table,
.playlist-edit-tracks,
.playlist-add-card {
  border-color: var(--border-soft);
}

/* Solid primary surfaces follow the selected primary color. */
button.primary,
.transport-main,
.station-floating-play,
.lobby-inline-controls .round-control:not(:disabled),
.lobby-dashboard-shell .lobby-station-play-button {
  background: var(--accent) !important;
  border-color: var(--accent-border) !important;
  color: var(--accent-contrast) !important;
  box-shadow: 0 8px 24px var(--accent-shadow);
}

button.primary:hover:not(:disabled),
.transport-main:hover:not(:disabled),
.station-floating-play:hover:not(:disabled),
.lobby-inline-controls .round-control:hover:not(:disabled),
.lobby-dashboard-shell .lobby-station-play-button:hover:not(:disabled) {
  background: var(--accent-strong) !important;
}

/* Selected / active UI should not remain amber when the primary color changes. */
.app-sidebar .side-link.active,
.queue-row-redesign.active,
.settings-segmented button.active,
.search-source-tabs button.active,
.search-result-tabs button.active,
.add-source-tabs button.active,
.lobby-add-mode-tabs button.active {
  border-color: var(--accent-border) !important;
}

.app-sidebar .side-link.active {
  background: linear-gradient(90deg, var(--accent-soft), transparent) !important;
}

.queue-row-redesign.active {
  background: linear-gradient(90deg, var(--accent-soft), transparent) !important;
}

.app-sidebar .side-link.active::before,
.queue-row-redesign.active::before {
  background: var(--accent-bright) !important;
  box-shadow: 0 0 10px var(--accent-shadow) !important;
}

.app-sidebar .side-link.active .side-icon,
.eyebrow,
.queue-row-redesign.active .queue-main strong,
.queue-row-redesign.active .queue-duration,
.rating-button[data-active="true"] {
  color: var(--accent-bright) !important;
}

.scrub-input,
.volume-input,
.autoplay-toggle input {
  accent-color: var(--accent) !important;
}

.info-banner {
  background: var(--accent-soft) !important;
  border-color: var(--accent-border) !important;
  color: var(--text) !important;
}

.station-type-card.active,
.rating-button[data-active="true"] {
  background: var(--accent-soft) !important;
  border-color: var(--accent-border) !important;
}

/* Shared typography colors. */
.muted,
.queue-main .muted,
.now-playing-info .muted,
.settings-note,
.settings-section-heading p {
  color: var(--muted);
}

.queue-summary,
.queue-drag-placeholder,
.queue-duration,
.queue-remove-icon {
  color: var(--faint) !important;
}
\n${reduceMotionCss}${densityCss(settings.appearance_ui_density)}${artworkRadiusCss(settings.appearance_artwork_radius)}${queueDurationCss}${queueIndicatorCss}${artworkBackgroundCss}${customDisabled ? '' : settings.advanced_custom_css}`
}

export function UserThemeStyles() {
  const [payload, setPayload] = useState<UserSettingsPayload | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await api.userSettings()
        if (!cancelled) setPayload(next)
      } catch {
        /* theme falls back to bundled defaults */
      }
    }
    void load()
    const listener = (event: Event) => setPayload((event as CustomEvent<UserSettingsPayload>).detail)
    window.addEventListener('helix-user-settings-updated', listener)
    return () => {
      cancelled = true
      window.removeEventListener('helix-user-settings-updated', listener)
    }
  }, [])

  useEffect(() => {
    const density = payload?.settings.appearance_ui_density || 'comfortable'
    document.documentElement.dataset.helixDensity = density
    return () => { delete document.documentElement.dataset.helixDensity }
  }, [payload?.settings.appearance_ui_density])

  return <style id="helix-user-custom-css">{themeCss(payload)}</style>
}
