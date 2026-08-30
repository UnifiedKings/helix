import { useEffect, useRef } from 'react'
import type { Station, StationProviderInfo } from '../../api/types'
import { Artwork } from '../Artwork'
import { configSummary, providerLabel, relativeUpdatedTime } from './stationUtils'

export function StationCard({ station, provider, busy, starting, onPlay, onEdit, onDelete }: { station: Station; provider?: StationProviderInfo; busy: boolean; starting: boolean; onPlay: () => void; onEdit: () => void; onDelete: () => void }) {
  const menuRef = useRef<HTMLDetailsElement | null>(null)

  useEffect(() => {
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const menu = menuRef.current
      if (!menu?.open) return
      const target = event.target
      if (target instanceof Node && !menu.contains(target)) menu.open = false
    }

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && menuRef.current?.open) menuRef.current.open = false
    }

    document.addEventListener('pointerdown', closeOnOutsidePointer)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [])

  const runAndClose = (action: () => void) => {
    if (menuRef.current) menuRef.current.open = false
    action()
  }

  return <article className="station-card-redesign"><div className="station-art-wrap"><Artwork src={station.cover_url || station.thumbnail_url || `/api/stations/${station.id}/cover`} alt={station.name} size="lg" /><button className="station-floating-play" type="button" onClick={onPlay} disabled={starting} aria-label={`Play ${station.name}`}><span aria-hidden="true">▶</span></button></div><div className="station-card-body"><span className="station-type-badge">{providerLabel(provider, station.station_type)}</span><h3>{station.name}</h3><p className="muted">{configSummary(station, provider)}</p><div className="station-card-footer"><span>{relativeUpdatedTime(station.updated_at)}</span><details ref={menuRef} className="station-card-menu"><summary aria-label={`Station actions for ${station.name}`}>•••</summary><div className="station-card-menu-popover"><button type="button" onClick={() => runAndClose(onEdit)}>Tune station</button><button className="danger" type="button" onClick={() => runAndClose(onDelete)} disabled={busy}>Delete station</button></div></details></div></div></article>
}
