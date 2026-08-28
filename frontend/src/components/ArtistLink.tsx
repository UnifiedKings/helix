import type { KeyboardEvent, MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

const resolvedArtistIds = new Map<string, string>()

type Props = {
  artist?: string | null
  className?: string
  browseId?: string | null
}

export function ArtistLink({ artist, className = '', browseId }: Props) {
  const navigate = useNavigate()
  const name = (artist || '').trim()

  async function openArtist(event?: MouseEvent<HTMLSpanElement> | KeyboardEvent<HTMLSpanElement>) {
    event?.preventDefault()
    event?.stopPropagation()
    if (!name) return

    let artistId = (browseId || '').trim()
    if (!artistId) artistId = resolvedArtistIds.get(name.toLocaleLowerCase()) || ''

    if (!artistId) {
      try {
        const payload = await api.searchArtists(name)
        const normalized = name.toLocaleLowerCase()
        const match = payload.artists.find((candidate) => candidate.name.trim().toLocaleLowerCase() === normalized) || payload.artists[0]
        artistId = String(match?.browse_id || match?.artist_id || '')
        if (artistId) resolvedArtistIds.set(normalized, artistId)
      } catch {
        return
      }
    }

    if (artistId) navigate(`/artists/${encodeURIComponent(artistId)}`)
  }

  if (!name) return null

  return (
    <span
      className={`artist-inline-link${className ? ` ${className}` : ''}`}
      role="link"
      tabIndex={0}
      title={`Open ${name}`}
      onClick={(event) => { void openArtist(event) }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') void openArtist(event)
      }}
    >
      {name}
    </span>
  )
}
