import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { UserSettings, UserSettingsPayload } from '../api/types'
import {
  builtInGoogleFontUrl,
  fontStackForId,
  type HelixFontId,
  validGoogleFontsUrl,
} from '../lib/fonts'

type FontSettings = UserSettings & {
  appearance_font_single: boolean
  appearance_font_ui: HelixFontId
  appearance_font_display: HelixFontId
  appearance_font_secondary: HelixFontId
  appearance_font_lyrics: HelixFontId
  appearance_google_font_url_ui: string
  appearance_google_font_url_display: string
  appearance_google_font_url_secondary: string
  appearance_google_font_url_lyrics: string
}

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
  if (density === 'compact') return `
:root { --transport-control-gap: 0.62rem; }
.side-link { min-height: 40px !important; padding-block: .55rem !important; }
.queue-row-redesign, .queue-panel-redesign .queue-item { min-height: 62px !important; }
.settings-control-row { padding-block: .62rem !important; }
.station-card-body { padding: .72rem .78rem !important; }
.search-song-row, .history-row { min-height: 58px !important; }
`
  if (density === 'spacious') return `
:root { --transport-control-gap: 1.28rem; }
.side-link { min-height: 52px !important; padding-block: .86rem !important; }
.queue-row-redesign, .queue-panel-redesign .queue-item { min-height: 78px !important; }
.settings-control-row { padding-block: 1rem !important; }
.station-card-body { padding: 1rem 1.05rem !important; }
.search-song-row, .history-row { min-height: 72px !important; }
`
  return `:root { --transport-control-gap: 0.92rem; }`
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
  const settings = payload.settings as FontSettings
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

  const uiFont = settings.appearance_font_ui || 'jetbrains-mono'
  const displayFont = settings.appearance_font_single ? uiFont : (settings.appearance_font_display || uiFont)
  const secondaryFont = settings.appearance_font_single ? uiFont : (settings.appearance_font_secondary || uiFont)
  const lyricsFont = settings.appearance_font_single ? uiFont : (settings.appearance_font_lyrics || uiFont)
  const uiExternalUrl = settings.appearance_google_font_url_ui || ''
  const displayExternalUrl = settings.appearance_google_font_url_display || ''
  const secondaryExternalUrl = settings.appearance_google_font_url_secondary || ''
  const lyricsExternalUrl = settings.appearance_google_font_url_lyrics || ''

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

  --font-ui: ${fontStackForId(uiFont, uiExternalUrl)};
  --font-display: ${fontStackForId(displayFont, settings.appearance_font_single ? uiExternalUrl : displayExternalUrl)};
  --font-secondary: ${fontStackForId(secondaryFont, settings.appearance_font_single ? uiExternalUrl : secondaryExternalUrl)};
  --font-lyrics: ${fontStackForId(lyricsFont, settings.appearance_font_single ? uiExternalUrl : lyricsExternalUrl)};
}

/* Typography compatibility layer.
   Older Helix styles sometimes set font-family directly. These rules make the
   user's typography selection authoritative rather than relying on inheritance. */
#root,
#root button,
#root input,
#root select,
#root textarea,
#root label,
#root a,
#root p,
#root span,
#root small,
#root strong,
#root b,
#root em,
#root time,
#root output,
#root code,
#root pre,
#root summary,
#root li,
#root td,
#root th {
  font-family: var(--font-ui) !important;
}

#root h1,
#root h2,
#root h3,
#root h4,
#root h5,
#root h6,
#root .home-session-title-row h1,
#root .big-picture-title,
#root .big-picture-track-title,
#root .stations-hero h1,
#root .playlist-editor-heading h1,
#root .lobby-dashboard-title-row h1,
#root .detail-copy h1,
#root .lobby-room-header h1,
#root .search-hero h1 {
  font-family: var(--font-display) !important;
}

#root .muted,
#root .now-playing-meta,
#root .home-session-copy > .muted,
#root .home-session-artist-row,
#root .home-session-station,
#root .home-session-meta,
#root .album-detail-meta,
#root .album-track-artist,
#root .queue-main .muted,
#root .queue-artist,
#root .queue-duration,
#root .history-row .muted,
#root .search-result-meta,
#root .detail-meta,
#root .settings-note,
#root .settings-section-heading p,
#root .playback-meta,
#root .now-playing-info .muted {
  font-family: var(--font-secondary) !important;
}

#root .lyrics-panel,
#root .lyrics-panel *,
#root .big-picture-lyrics-stage,
#root .big-picture-lyrics-stage *,
#root .big-picture-lyrics-line,
#root .lyrics-line {
  font-family: var(--font-lyrics) !important;
}

/* The S+ mark is a positioned glyph, not normal interface copy. */
#root .splus-s,
#root .splus-plus,
#root .splus-glyph,
#root .splus-glyph text {
  font-family: Arial, Helvetica, sans-serif !important;
}

html, body, #root {
  background-color: var(--bg);
  color: var(--text);
}

.app-shell,
.app-shell-with-sidebar,
.dashboard-grid,
.dashboard-content-card,
main { color: var(--text); }

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

${reduceMotionCss}${densityCss(settings.appearance_ui_density)}${artworkRadiusCss(settings.appearance_artwork_radius)}${queueDurationCss}${queueIndicatorCss}${artworkBackgroundCss}${customDisabled ? '' : settings.advanced_custom_css}`
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

  useEffect(() => {
    if (!payload) return
    const settings = payload.settings as FontSettings
    const ui = settings.appearance_font_ui || 'jetbrains-mono'

    const selections = settings.appearance_font_single
      ? [
          {
            slot: 'ui',
            id: ui,
            externalUrl: settings.appearance_google_font_url_ui || '',
          },
        ]
      : [
          {
            slot: 'ui',
            id: ui,
            externalUrl: settings.appearance_google_font_url_ui || '',
          },
          {
            slot: 'display',
            id: settings.appearance_font_display || ui,
            externalUrl: settings.appearance_google_font_url_display || '',
          },
          {
            slot: 'secondary',
            id: settings.appearance_font_secondary || ui,
            externalUrl: settings.appearance_google_font_url_secondary || '',
          },
          {
            slot: 'lyrics',
            id: settings.appearance_font_lyrics || ui,
            externalUrl: settings.appearance_google_font_url_lyrics || '',
          },
        ]

    const keepIds = new Set<string>()

    selections.forEach(({ slot, id, externalUrl }) => {
      const elementId = `helix-google-font-${slot}`
      keepIds.add(elementId)

      const url = id === 'custom-google'
        ? (validGoogleFontsUrl(externalUrl) ? externalUrl : '')
        : builtInGoogleFontUrl(id)

      let link = document.head.querySelector<HTMLLinkElement>(`#${elementId}`)

      if (!url) {
        link?.remove()
        return
      }

      if (!link) {
        link = document.createElement('link')
        link.id = elementId
        link.rel = 'stylesheet'
        document.head.appendChild(link)
      }

      if (link.href !== url) link.href = url
    })

    document.head.querySelectorAll<HTMLLinkElement>('link[id^="helix-google-font-"]').forEach((link) => {
      if (!keepIds.has(link.id)) link.remove()
    })
  }, [payload])

  return <style id="helix-user-custom-css">{themeCss(payload)}</style>
}
