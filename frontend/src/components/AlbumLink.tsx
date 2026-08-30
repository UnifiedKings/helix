import type { KeyboardEvent, MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

const resolvedAlbums = new Map<string, { id: string; source?: string }>()

type Props = {
  album?: string | null
  artist?: string | null
  albumId?: string | null
  source?: string | null
  className?: string
}

function albumCacheKey(album: string, artist?: string | null) {
  return `${album.trim().toLocaleLowerCase()}::${(artist || '').trim().toLocaleLowerCase()}`
}

function normalized(value?: string | null) {
  return (value || '').trim().toLocaleLowerCase()
}

export function AlbumLink({ album, artist, albumId, source, className = '' }: Props) {
  const navigate = useNavigate()
  const title = (album || '').trim()
  const artistName = (artist || '').trim()

  async function openAlbum(event?: MouseEvent<HTMLSpanElement> | KeyboardEvent<HTMLSpanElement>) {
    event?.preventDefault()
    event?.stopPropagation()
    if (!title) return

    let resolvedId = (albumId || '').trim()
    let resolvedSource = (source || '').trim() || undefined
    const cacheKey = albumCacheKey(title, artistName)
    if (!resolvedId) {
      const cached = resolvedAlbums.get(cacheKey)
      if (cached) {
        resolvedId = cached.id
        resolvedSource = cached.source
      }
    }

    if (!resolvedId) {
      try {
        const normalizedTitle = normalized(title)
        const normalizedArtist = normalized(artistName)
        const searchQueries = artistName ? [`${artistName} ${title}`, title] : [title]

        for (const query of searchQueries) {
          const payload = await api.search(query, 'hybrid')
          const exactTitleMatches = payload.albums.filter(
            (candidate) => normalized(candidate.title) === normalizedTitle,
          )

          const match = normalizedArtist
            ? exactTitleMatches.find((candidate) => normalized(candidate.artist) === normalizedArtist)
            : exactTitleMatches[0] || payload.albums[0]

          if (!match) continue

          resolvedId = String(
            match.yt_browse_id ||
            match.browse_id ||
            match.browseId ||
            match.subsonic_album_id ||
            '',
          )
          resolvedSource = match.source || resolvedSource
          if (resolvedId) {
            resolvedAlbums.set(cacheKey, { id: resolvedId, source: resolvedSource })
            break
          }
        }
      } catch {
        return
      }
    }

    if (!resolvedId) return
    const path = `/albums/${encodeURIComponent(resolvedId)}`
    navigate(resolvedSource === 'subsonic' ? `${path}?source=subsonic` : path)
  }

  if (!title) return null

  return (
    <span
      className={`artist-inline-link album-inline-link${className ? ` ${className}` : ''}`}
      role="link"
      tabIndex={0}
      title={`Open ${title}`}
      onClick={(event) => { void openAlbum(event) }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') void openAlbum(event)
      }}
    >
      {title}
    </span>
  )
}
