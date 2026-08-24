import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { UserSettingsPayload } from '../api/types'

type ImportToast = {
  id: number
  title?: string
  kind?: 'track' | 'album'
}

type ImportQueuedDetail = {
  title?: string
  kind?: 'track' | 'album'
}

const LIFETIMES = {
  short: 1400,
  normal: 2200,
  long: 4000,
} as const

export function ImportQueuedToast() {
  const [toasts, setToasts] = useState<ImportToast[]>([])
  const [settings, setSettings] = useState<UserSettingsPayload['settings'] | null>(null)
  const nextId = useRef(1)

  useEffect(() => {
    let cancelled = false
    void api.userSettings().then((payload) => {
      if (!cancelled) setSettings(payload.settings)
    }).catch(() => {
      /* defaults below keep the toast functional if settings cannot be loaded */
    })

    const onSettings = (event: Event) => {
      const payload = (event as CustomEvent<UserSettingsPayload>).detail
      if (payload?.settings) setSettings(payload.settings)
    }
    window.addEventListener('helix-user-settings-updated', onSettings)
    return () => {
      cancelled = true
      window.removeEventListener('helix-user-settings-updated', onSettings)
    }
  }, [])

  useEffect(() => {
    const onQueued = (event: Event) => {
      if (settings?.notifications_import_queued === false) return
      const detail = (event as CustomEvent<ImportQueuedDetail>).detail ?? {}
      const id = nextId.current++
      const duration = LIFETIMES[settings?.notifications_duration ?? 'normal'] ?? LIFETIMES.normal
      setToasts((current) => [...current.slice(-2), { id, title: detail.title, kind: detail.kind }])

      window.setTimeout(() => {
        setToasts((current) => current.filter((toast) => toast.id !== id))
      }, duration)
    }

    window.addEventListener('helix:subsonic-import-queued', onQueued)
    return () => window.removeEventListener('helix:subsonic-import-queued', onQueued)
  }, [settings])

  if (!toasts.length) return null

  return (
    <div className="import-toast-stack" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <div className="import-toast" key={toast.id} role="status">
          <span className="import-toast-icon" aria-hidden="true">✓</span>
          <span className="import-toast-copy">
            <strong>Import request queued</strong>
            {toast.title ? <span>{toast.kind === 'album' ? 'Album' : 'Track'} · {toast.title}</span> : null}
          </span>
        </div>
      ))}
    </div>
  )
}
