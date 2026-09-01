export type HelixFontId =
  | 'jetbrains-mono'
  | 'ibm-plex-mono'
  | 'fira-code'
  | 'source-code-pro'
  | 'space-mono'
  | 'inter'
  | 'ibm-plex-sans'
  | 'space-grotesk'
  | 'custom-google'

export type TypographySettingsShape = {
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

export const HELIX_FONTS = [
  { id: 'jetbrains-mono', label: 'JetBrains Mono', family: 'JetBrains Mono', googleFamily: 'JetBrains+Mono:wght@400;500;600;700', mono: true },
  { id: 'ibm-plex-mono', label: 'IBM Plex Mono', family: 'IBM Plex Mono', googleFamily: 'IBM+Plex+Mono:wght@400;500;600;700', mono: true },
  { id: 'fira-code', label: 'Fira Code', family: 'Fira Code', googleFamily: 'Fira+Code:wght@400;500;600;700', mono: true },
  { id: 'source-code-pro', label: 'Source Code Pro', family: 'Source Code Pro', googleFamily: 'Source+Code+Pro:wght@400;500;600;700', mono: true },
  { id: 'space-mono', label: 'Space Mono', family: 'Space Mono', googleFamily: 'Space+Mono:ital,wght@0,400;0,700;1,400;1,700', mono: true },
  { id: 'inter', label: 'Inter', family: 'Inter', googleFamily: 'Inter:wght@400;500;600;700', mono: false },
  { id: 'ibm-plex-sans', label: 'IBM Plex Sans', family: 'IBM Plex Sans', googleFamily: 'IBM+Plex+Sans:wght@400;500;600;700', mono: false },
  { id: 'space-grotesk', label: 'Space Grotesk', family: 'Space Grotesk', googleFamily: 'Space+Grotesk:wght@400;500;600;700', mono: false },
] as const

export const DEFAULT_HELIX_FONT: HelixFontId = 'jetbrains-mono'

export function isHelixFontId(value: unknown): value is HelixFontId {
  return value === 'custom-google' || HELIX_FONTS.some((font) => font.id === value)
}

export function googleFontFamilyFromUrl(rawUrl: string): string {
  const raw = (rawUrl || '').trim()
  if (!raw) return ''
  try {
    const url = new URL(raw)
    if (url.protocol !== 'https:' || url.hostname !== 'fonts.googleapis.com') return ''
    const family = url.searchParams.get('family') || ''
    return family.split('|')[0].split(':')[0].replace(/\+/g, ' ').trim()
  } catch {
    return ''
  }
}

export function validGoogleFontsUrl(rawUrl: string): boolean {
  if (!rawUrl.trim()) return false
  try {
    const url = new URL(rawUrl)
    return url.protocol === 'https:'
      && url.hostname === 'fonts.googleapis.com'
      && Boolean(url.searchParams.get('family'))
  } catch {
    return false
  }
}

export function fontFamilyForId(id: string, customGoogleUrl = ''): string {
  if (id === 'custom-google') return googleFontFamilyFromUrl(customGoogleUrl)
  return HELIX_FONTS.find((font) => font.id === id)?.family || 'JetBrains Mono'
}

export function fontStackForId(id: string, customGoogleUrl = ''): string {
  const family = fontFamilyForId(id, customGoogleUrl)
  const builtin = HELIX_FONTS.find((font) => font.id === id)
  const mono = id === 'custom-google' ? true : (builtin?.mono ?? true)
  const fallback = mono
    ? 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace'
    : 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
  return family ? `"${family.replace(/"/g, '')}", ${fallback}` : fallback
}

export function builtInGoogleFontUrl(id: string): string {
  if (id === 'custom-google') return ''
  const font = HELIX_FONTS.find((entry) => entry.id === id)
  if (!font) return ''
  return `https://fonts.googleapis.com/css2?family=${font.googleFamily}&display=swap`
}
