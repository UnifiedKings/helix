import { useEffect, useState } from 'react'
import '../styles/shares.css'

export type ShareTarget = {
  resourceType: 'track' | 'album' | 'playlist'
  resourceId: string
  resourceSource: 'subsonic' | 'ytmusic' | 'helix'
  title: string
  artist?: string
  album?: string
  durationMs?: number
  artUrl?: string
}

type ShareConfig = {
  expiration_days: number[]
  default_expiration_days: number
}

type CreatedShare = {
  token: string
  url: string
}

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options,
  })
  const text = await response.text()
  if (!response.ok) {
    let detail = text || `${response.status} ${response.statusText}`
    try {
      detail = JSON.parse(text).detail ?? detail
    } catch {
      // Keep the raw response text.
    }
    throw new Error(detail)
  }
  return text ? JSON.parse(text) as T : undefined as T
}

function expirationLabel(days: number) {
  if (days === 0) return 'Never'
  if (days === 1) return '1 day'
  return `${days} days`
}

export function ShareLinkModal({ target, onClose }: { target: ShareTarget | null; onClose: () => void }) {
  const [config, setConfig] = useState<ShareConfig>({ expiration_days: [1, 7, 30, 365, 0], default_expiration_days: 30 })
  const [days, setDays] = useState(30)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [createdUrl, setCreatedUrl] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!target) return
    setBusy(false)
    setError('')
    setCreatedUrl('')
    setCopied(false)
    let cancelled = false
    void requestJson<ShareConfig>('/api/shares/config')
      .then((next) => {
        if (cancelled) return
        setConfig(next)
        setDays(next.default_expiration_days)
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [target])

  useEffect(() => {
    if (!target) return
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) onClose()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [target, busy, onClose])

  if (!target) return null

  async function copyUrl(url: string) {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  async function createShare() {
    const shareTarget = target
    if (!shareTarget) return

    if (!shareTarget.resourceId) {
      setError('This item is missing a playable source ID and cannot be shared yet.')
      return
    }

    setBusy(true)
    setError('')
    try {
      const share = await requestJson<CreatedShare>('/api/shares', {
        method: 'POST',
        body: JSON.stringify({
          resource_type: shareTarget.resourceType,
          resource_id: shareTarget.resourceId,
          resource_source: shareTarget.resourceSource,
          expiration_days: days,
          title: shareTarget.title,
          artist: shareTarget.artist || '',
          album: shareTarget.album || '',
          duration_ms: shareTarget.durationMs || 0,
          art_url: shareTarget.artUrl || '',
        }),
      })
      const absoluteUrl = `${window.location.origin}${share.url}`
      setCreatedUrl(absoluteUrl)
      await copyUrl(absoluteUrl)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create share link')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="share-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose()
      }}
    >
      <section className="share-modal" role="dialog" aria-modal="true" aria-labelledby="share-modal-title">
        <header className="share-modal-header">
          <div>
            <span className="eyebrow">Share {target.resourceType}</span>
            <h2 id="share-modal-title">{target.title}</h2>
            {target.artist ? <p>{target.artist}</p> : null}
          </div>
          <button type="button" className="share-modal-close" onClick={onClose} disabled={busy} aria-label="Close share dialog">×</button>
        </header>

        <div className="share-modal-preview">
          {target.artUrl ? <img src={target.artUrl} alt="" /> : <span className="share-modal-art-placeholder" aria-hidden="true" />}
          <div>
            <strong>{target.title}</strong>
            {target.artist ? <span>{target.artist}</span> : null}
            {target.album ? <small>{target.album}</small> : null}
          </div>
        </div>

        {error ? <div className="error-banner">{error}</div> : null}

        {!createdUrl ? (
          <label className="share-modal-expiration">
            <span>Link expires</span>
            <select value={days} onChange={(event) => setDays(Number(event.target.value))} disabled={busy}>
              {config.expiration_days.map((value) => <option key={value} value={value}>{expirationLabel(value)}</option>)}
            </select>
          </label>
        ) : (
          <div className="share-modal-created">
            <span>Share link</span>
            <div>
              <input value={createdUrl} readOnly onFocus={(event) => event.currentTarget.select()} />
              <button type="button" onClick={() => void copyUrl(createdUrl)}>{copied ? 'Copied' : 'Copy'}</button>
            </div>
          </div>
        )}

        <footer className="share-modal-footer">
          <button type="button" onClick={onClose} disabled={busy}>{createdUrl ? 'Done' : 'Cancel'}</button>
          {!createdUrl ? <button type="button" className="primary" onClick={() => void createShare()} disabled={busy}>{busy ? 'Creating…' : 'Create & copy link'}</button> : null}
        </footer>
      </section>
    </div>
  )
}
