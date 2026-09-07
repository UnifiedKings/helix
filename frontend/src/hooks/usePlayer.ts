import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { AudioIntent, PlayerState } from '../api/types'

export type AudioRunMode = 'play' | 'pause' | 'none'

export function usePlayer() {
  const [player, setPlayer] = useState<PlayerState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [audioIntent, setAudioIntent] = useState<AudioIntent>({ id: 0, action: 'pause' })
  const [transportBusy, setTransportBusy] = useState(false)
  const latestRequestRef = useRef(0)
  const actionInFlightRef = useRef(false)
  const transportInFlightRef = useRef<Promise<PlayerState> | null>(null)
  const socketOpenRef = useRef(false)
  const lastSocketSeqRef = useRef(0)

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

  const run = useCallback((action: () => Promise<PlayerState>, audioMode: AudioRunMode = 'none') => {
    const isDirectTransport = action === api.pause || action === api.resume

    // A direct play/pause command owns the transport until its backend request
    // settles. Ignore additional direct transport clicks completely, including
    // their local audio intent, so button-spam cannot make browser audio and the
    // authoritative backend state diverge.
    if (isDirectTransport && transportInFlightRef.current) {
      return transportInFlightRef.current
    }

    const execute = async () => {
      const requestId = ++latestRequestRef.current
      actionInFlightRef.current = true

      try {
        setError('')

        // Pause should be audible immediately. Do this only after the transport
        // lock has accepted the command so ignored spam cannot toggle local audio.
        if (audioMode === 'pause' && action === api.pause) {
          setAudioIntent((current) => ({ id: current.id + 1, action: 'pause' }))
        }

        const next = await action()

        if (requestId === latestRequestRef.current) {
          setPlayer(next)
        }

        // Starting playback remains backend-first. Non-direct actions such as
        // next/previous retain their existing post-action audio behavior.
        if (audioMode === 'play') {
          setAudioIntent((current) => ({ id: current.id + 1, action: 'play' }))
        } else if (audioMode === 'pause' && action !== api.pause) {
          setAudioIntent((current) => ({ id: current.id + 1, action: 'pause' }))
        }

        return next
      } catch (err) {
        if (requestId === latestRequestRef.current) {
          setError(err instanceof Error ? err.message : 'Playback action failed')
        }
        throw err
      } finally {
        if (requestId === latestRequestRef.current) {
          actionInFlightRef.current = false
          setLoading(false)
        }
      }
    }

    if (!isDirectTransport) {
      return execute()
    }

    const transportPromise = execute()
    transportInFlightRef.current = transportPromise
    setTransportBusy(true)

    void transportPromise.finally(() => {
      if (transportInFlightRef.current === transportPromise) {
        transportInFlightRef.current = null
        setTransportBusy(false)
      }
    }).catch(() => undefined)

    return transportPromise
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
          const message = JSON.parse(event.data) as { type?: string; seq?: number; state?: PlayerState }
          if (message.type !== 'player.state' || !message.state) return

          const seq = Number(message.seq || 0)
          if (seq && seq <= lastSocketSeqRef.current) return
          if (seq) lastSocketSeqRef.current = seq

          if (actionInFlightRef.current) return

          setPlayer(message.state)
          setLoading(false)
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

  return { player, loading, error, refresh, run, setPlayer, setError, audioIntent, transportBusy }
}
