import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { AudioIntent, PlayerState } from '../api/types'

export type AudioRunMode = 'play' | 'pause' | 'none'

export function usePlayer() {
  const [player, setPlayer] = useState<PlayerState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [audioIntent, setAudioIntent] = useState<AudioIntent>({ id: 0, action: 'pause' })

  const refresh = useCallback(async () => {
    try {
      setError('')
      setPlayer(await api.playerState())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load player state')
    } finally {
      setLoading(false)
    }
  }, [])

  const run = useCallback(async (action: () => Promise<PlayerState>, audioMode: AudioRunMode = 'none') => {
    try {
      setError('')
      const next = await action()
      setPlayer(next)
      if (audioMode === 'play' || audioMode === 'pause') {
        setAudioIntent((current) => ({ id: current.id + 1, action: audioMode }))
      }
      return next
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Playback action failed')
      throw err
    }
  }, [])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(refresh, 3000)
    return () => window.clearInterval(interval)
  }, [refresh])

  return { player, loading, error, refresh, run, setPlayer, setError, audioIntent }
}
