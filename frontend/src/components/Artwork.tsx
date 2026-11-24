import { useEffect, useMemo, useState } from 'react'

type Props = {
  src?: string
  alt: string
  size?: 'sm' | 'md' | 'lg'
  artist?: string
  album?: string
  subsonicSongId?: string
}

// A previously stored image URL may be dead, and older liked tracks can have
// no URL despite being present in Subsonic. Ask Helix to resolve the cover art
// only in those cases; never replace successfully loaded artwork.
export function Artwork({ src, alt, size = 'md', artist, album, subsonicSongId }: Props) {
  const primary = (src || '').trim()
  const fallback = useMemo(() => {
    if (!alt && !subsonicSongId) return ''
    const params = new URLSearchParams({ size: size === 'sm' ? '128' : '512' })
    if (subsonicSongId) params.set('subsonic_song_id', subsonicSongId)
    if (alt) params.set('title', alt)
    if (artist) params.set('artist', artist)
    if (album) params.set('album', album)
    return `/api/art/resolve?${params.toString()}`
  }, [alt, artist, album, size, subsonicSongId])
  const [failedPrimary, setFailedPrimary] = useState(false)
  const [failedFallback, setFailedFallback] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    setFailedPrimary(false)
    setFailedFallback(false)
    setLoaded(false)
  }, [primary, fallback])

  const useFallback = !primary || failedPrimary
  const displaySrc = useFallback ? (failedFallback ? '' : fallback) : primary

  if (!displaySrc) return <div className={`artwork artwork-${size} artwork-empty`}>♪</div>

  return (
    <div className={`artwork artwork-${size}`} style={{ position: 'relative', overflow: 'hidden' }}>
      {!loaded ? <div className={`artwork artwork-${size} artwork-empty`} style={{ position: 'absolute', inset: 0 }}>♪</div> : null}
      <img
        key={displaySrc}
        src={displaySrc}
        alt={alt}
        className={`artwork artwork-${size}`}
        style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: loaded ? 1 : 0 }}
        onLoad={() => setLoaded(true)}
        onError={() => {
          setLoaded(false)
          if (useFallback) setFailedFallback(true)
          else setFailedPrimary(true)
        }}
      />
    </div>
  )
}
