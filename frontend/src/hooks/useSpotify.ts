import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { SpotifyConnectionStatus } from '../api/types'

const OAUTH_MESSAGE_SOURCE = 'helix:spotify-oauth'

export type SpotifyConnectResult =
  | { status: 'ok' }
  | { status: 'error'; message: string }
  | { status: 'blocked' }
  | { status: 'closed' }

type OAuthMessage = {
  source?: string
  status?: string
  error?: string
  display_name?: string
}

/**
 * Per-user Spotify OAuth connection state shared by the import dialog and
 * Settings. Connect opens the Spotify authorization pop-up and resolves once
 * the pop-up has bounced back through the backend's /spotify-oauth.html page.
 */
export function useSpotify(enabled = true) {
  const [status, setStatus] = useState<SpotifyConnectionStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const popupRef = useRef<Window | null>(null)
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled

  useEffect(() => {
    if (!enabledRef.current) return
    let cancelled = false
    setLoading(true)
    api.spotifyStatus()
      .then((next) => { if (!cancelled) setStatus(next) })
      .catch(() => { if (!cancelled) setStatus(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const refresh = useCallback(async (): Promise<SpotifyConnectionStatus> => {
    setLoading(true)
    try {
      const next = await api.spotifyStatus()
      setStatus(next)
      return next
    } finally {
      setLoading(false)
    }
  }, [])

  const connect = useCallback(async (): Promise<SpotifyConnectResult> => {
    setConnecting(true)
    try {
      const { oauth_url } = await api.spotifyAuthStart()
      const popup = window.open(oauth_url, '_blank', 'width=560,height=720')
      if (!popup) {
        popupRef.current = null
        return { status: 'blocked' }
      }
      popupRef.current = popup

      return await new Promise<SpotifyConnectResult>((resolve) => {
        function cleanup() {
          window.removeEventListener('message', onMessage)
          window.clearInterval(closeCheck)
        }
        function closePopup() {
          if (popupRef.current && !popupRef.current.closed) {
            try { popupRef.current.close() } catch { /* same-origin popup only */ }
          }
          popupRef.current = null
        }
        function onMessage(event: MessageEvent) {
          const data = event.data as OAuthMessage | null | undefined
          if (!data || typeof data !== 'object' || data.source !== OAUTH_MESSAGE_SOURCE) return
          cleanup()
          closePopup()
          setConnecting(false)
          if (data.status === 'ok') {
            void refresh().then(() => resolve({ status: 'ok' }))
          } else {
            resolve({ status: 'error', message: data.error || 'Spotify did not approve the connection.' })
          }
        }
        const closeCheck = window.setInterval(() => {
          if (popup.closed) {
            cleanup()
            popupRef.current = null
            setConnecting(false)
            resolve({ status: 'closed' })
          }
        }, 400)
        window.addEventListener('message', onMessage)
      })
    } finally {
      setConnecting(false)
    }
  }, [])

  const disconnect = useCallback(async () => {
    await api.spotifyDisconnect()
    setStatus((current) => ({ connected: false, configured: current?.configured ?? true }))
  }, [])

  return { status, loading, connecting, refresh, connect, disconnect }
}