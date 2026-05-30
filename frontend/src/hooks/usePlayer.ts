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

  const refresh = useCallback(async () => {
    // Do not let the polling request race against a transport action. This was
    // causing older playback state snapshots to overwrite skip/previous results.
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
      if (requestId !== latestRequestRef.current) return next
      setPlayer(next)
      if (audioMode === 'play' || audioMode === 'pause') {
        setAudioIntent((current) => ({ id: current.id + 1, action: audioMode }))
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
  }, [])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(refresh, 3000)
    return () => window.clearInterval(interval)
  }, [refresh])

  return { player, loading, error, refresh, run, setPlayer, setError, audioIntent }
}
