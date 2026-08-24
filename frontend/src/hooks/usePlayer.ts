import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { AudioIntent, PlayerState } from '../api/types'

export type AudioRunMode = 'play' | 'pause' | 'none'

export function usePlayer() {
  const [player, setPlayer] = useState<PlayerState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [audioIntent, setAudioIntent] = useState<AudioIntent>({ id: 0, action: 'pause' })
  const latestRequestRef = useRef(0)
  const actionInFlightRef = useRef(false)
  const socketOpenRef = useRef(false)

  const refresh = useCallback(async () => {
    if (actionInFlightRef.current) return
    const requestId = ++latestRequestRef.current
    try {
      setError('')
      const next = await api.playerState()
      if (requestId !== latestRequestRef.current || actionInFlightRef.current) return
      setPlayer(next)
    } catch (err) {
      if (requestId !== latestRequestRef.current) return
      setError(err instanceof Error ? err.message : 'Could not load player state')
    } finally {
      if (requestId === latestRequestRef.current) setLoading(false)
    }
  }, [])

  const run = useCallback(async (action: () => Promise<PlayerState>, audioMode: AudioRunMode = 'none') => {
    const requestId = ++latestRequestRef.current
    actionInFlightRef.current = true
    try {
      setError('')
      const next = await action()
      if (requestId === latestRequestRef.current) setPlayer(next)
      if (audioMode === 'play' || audioMode === 'pause') {
        setAudioIntent((current) => ({ id: current.id + 1, action: audioMode }))
      }
      return next
    } catch (err) {
      if (requestId === latestRequestRef.current) setError(err instanceof Error ? err.message : 'Playback action failed')
      throw err
    } finally {
      if (requestId === latestRequestRef.current) {
        actionInFlightRef.current = false
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    void refresh()
    let socket: WebSocket | null = null
    let reconnectTimer = 0
    let pingTimer = 0
    let stopped = false

    const connect = () => {
      if (stopped) return
      socket = new WebSocket(api.playerSocketUrl())
      socket.onopen = () => {
        socketOpenRef.current = true
        setError('')
        pingTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send('ping')
        }, 20000)
      }
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as { type?: string; state?: PlayerState }
          if (message.type === 'player.state' && message.state) {
            setPlayer(message.state)
            setLoading(false)
          }
        } catch { /* ignore malformed realtime messages */ }
      }
      socket.onclose = () => {
        socketOpenRef.current = false
        window.clearInterval(pingTimer)
        if (!stopped) reconnectTimer = window.setTimeout(connect, 1500)
      }
      socket.onerror = () => socket?.close()
    }
    connect()

    // WebSocket is primary. This slow fallback only matters while disconnected.
    const fallback = window.setInterval(() => {
      if (!socketOpenRef.current) void refresh()
    }, 15000)

    return () => {
      stopped = true
      socketOpenRef.current = false
      window.clearInterval(fallback)
      window.clearInterval(pingTimer)
      window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [refresh])

  return { player, loading, error, refresh, run, setPlayer, setError, audioIntent }
}
