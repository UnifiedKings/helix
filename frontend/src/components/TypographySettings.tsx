import type { CSSProperties } from 'react'
import type { UserSettings } from '../api/types'
import {
  DEFAULT_HELIX_FONT,
  HELIX_FONTS,
  fontStackForId,
  googleFontFamiliesFromUrl,
  googleFontFamilyFromUrl,
  isHelixFontId,
  type HelixFontId,
  validGoogleFontsUrl,
} from '../lib/fonts'

type Settings = UserSettings & {
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

type Props = {
  settings: Settings
  onChange: (key: keyof Settings, value: unknown) => void
}

type ExternalUrlKey =
  | 'appearance_google_font_url_ui'
  | 'appearance_google_font_url_display'
  | 'appearance_google_font_url_secondary'
  | 'appearance_google_font_url_lyrics'

function selectedFont(value: unknown): HelixFontId {
  return isHelixFontId(value) ? value : DEFAULT_HELIX_FONT
}

function FontSelect({
  label,
  description,
  value,
  externalUrl,
  fontKey,
  externalUrlKey,
  onChange,
}: {
  label: string
  description: string
  value: HelixFontId
  externalUrl: string
  fontKey:
    | 'appearance_font_ui'
    | 'appearance_font_display'
    | 'appearance_font_secondary'
    | 'appearance_font_lyrics'
  externalUrlKey: ExternalUrlKey
  onChange: (key: keyof Settings, value: unknown) => void
}) {
  const externalSelected = value === 'custom-google'
  const externalFamilies = googleFontFamiliesFromUrl(externalUrl)
  const externalFamily = googleFontFamilyFromUrl(externalUrl)
  const externalValid = validGoogleFontsUrl(externalUrl)

  return (
    <div className="settings-font-option">
      <label className="settings-control-row">
        <div>
          <strong>{label}</strong>
          <span>{description}</span>
        </div>

        <span className="settings-font-control">
          <select value={value} onChange={(event) => onChange(fontKey, event.target.value as HelixFontId)}>
            {HELIX_FONTS.map((font) => (
              <option value={font.id} key={font.id}>{font.label}</option>
            ))}
            <option value="custom-google">External Google Font</option>
          </select>

          <span
            className="settings-font-preview"
            style={{ '--settings-font-preview-family': fontStackForId(value, externalUrl) } as CSSProperties}
          >
            The quick brown fox jumps over the lazy dog.
          </span>

          {externalSelected ? (
            <>
              <input
                className="settings-external-font-input"
                type="url"
                aria-label={`${label} external Google Font URL`}
                value={externalUrl}
                placeholder="Google Fonts CSS URL"
                onChange={(event) => onChange(externalUrlKey, event.target.value)}
              />
              <span className={`settings-font-url-status ${externalUrl && !externalValid ? 'invalid' : ''}`}>
                {!externalUrl
                  ? 'Paste a Google Fonts CSS URL.'
                  : externalValid
                    ? <>Detected: <strong>{externalFamily}</strong>{externalFamilies.length > 1 ? ` (${externalFamilies.length} families in URL; using the last)` : ''}</>
                    : 'Invalid Google Fonts CSS URL.'}
              </span>
            </>
          ) : null}
        </span>
      </label>
    </div>
  )
}

export function TypographySettings({ settings, onChange }: Props) {
  const single = settings.appearance_font_single ?? true
  const uiFont = selectedFont(settings.appearance_font_ui)

  return (
    <div className="settings-card">
      <div className="settings-card-heading-row">
        <div>
          <h3>Typography</h3>
          <p>Choose the fonts Helix uses. JetBrains Mono is the default.</p>
        </div>
      </div>

      <div className="settings-control-row">
        <div>
          <strong>Use one font everywhere</strong>
          <span>Apply the main font to display titles, secondary text, and lyrics.</span>
        </div>
        <button
          type="button"
          className={`settings-toggle ${single ? 'on' : ''}`}
          role="switch"
          aria-checked={single}
          onClick={() => onChange('appearance_font_single', !single)}
        >
          <span className="settings-toggle-thumb" />
        </button>
      </div>

      <FontSelect
        label={single ? 'Font' : 'UI / body'}
        description={single ? 'The font used throughout Helix.' : 'Navigation, controls, lists, buttons, and normal body text.'}
        value={uiFont}
        externalUrl={settings.appearance_google_font_url_ui || ''}
        fontKey="appearance_font_ui"
        externalUrlKey="appearance_google_font_url_ui"
        onChange={onChange}
      />

      {!single ? (
        <>
          <FontSelect
            label="Display / hero"
            description="Home and Big Picture song titles, page titles, and large headings."
            value={selectedFont(settings.appearance_font_display)}
            externalUrl={settings.appearance_google_font_url_display || ''}
            fontKey="appearance_font_display"
            externalUrlKey="appearance_google_font_url_display"
            onChange={onChange}
          />
          <FontSelect
            label="Secondary / subtitle"
            description="Artist names, album names, metadata, descriptions, and supporting text."
            value={selectedFont(settings.appearance_font_secondary)}
            externalUrl={settings.appearance_google_font_url_secondary || ''}
            fontKey="appearance_font_secondary"
            externalUrlKey="appearance_google_font_url_secondary"
            onChange={onChange}
          />
          <FontSelect
            label="Lyrics"
            description="Plain and synchronized lyrics, including Big Picture lyrics."
            value={selectedFont(settings.appearance_font_lyrics)}
            externalUrl={settings.appearance_google_font_url_lyrics || ''}
            fontKey="appearance_font_lyrics"
            externalUrlKey="appearance_google_font_url_lyrics"
            onChange={onChange}
          />
        </>
      ) : null}
    </div>
  )
}
