import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import type { PlaybackHistoryFilters, PlaybackHistoryItem, PlaybackHistoryResponse, Station } from '../api/types'
import { Artwork } from '../components/Artwork'
import type { usePlayer } from '../hooks/usePlayer'

type PlayerContext = ReturnType<typeof usePlayer>

const PAGE_SIZE = 100

type HistoryFilterState = {
  q: string
  artist: string
  album: string
  source: string
  event: string
  station_id: string
  date_from: string
  date_to: string
}

const EMPTY_FILTERS: HistoryFilterState = {
  q: '',
  artist: '',
  album: '',
  source: '',
  event: '',
  station_id: '',
  date_from: '',
  date_to: '',
}

function dateLabel(value: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function durationLabel(item: PlaybackHistoryItem) {
  const seconds = item.played_ms ? Math.round(item.played_ms / 1000) : item.duration_ms ? Math.round(item.duration_ms / 1000) : 0
  if (!seconds) return '—'
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, '0')}`
}

function sourceLabel(source?: string) {
  const value = (source ?? '').toLowerCase()
  if (value === 'subsonic') return 'Subsonic'
  if (value === 'ytmusic' || value === 'youtube') return 'YTMusic'
  if (value === 'inbound') return 'Inbound'
  return source || 'Unknown'
}

function resultLabel(item: PlaybackHistoryItem) {
  const event = item.event || 'played'
  return item.reason ? `${event} · ${item.reason}` : event
}

function PlayIcon() {
  return <span aria-hidden="true">▷</span>
}

function QueueIcon() {
  return <span aria-hidden="true">＋</span>
}

function ImportIcon() {
  return <span aria-hidden="true">⇩</span>
}

export function HistoryPage() {
  const player = useOutletContext<PlayerContext>()
  const [history, setHistory] = useState<PlaybackHistoryResponse | null>(null)
  const [stations, setStations] = useState<Station[]>([])
  const [filters, setFilters] = useState<HistoryFilterState>(EMPTY_FILTERS)
  const [appliedFilters, setAppliedFilters] = useState<HistoryFilterState>(EMPTY_FILTERS)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [subsonicAvailability, setSubsonicAvailability] = useState<Record<string, boolean>>({})
  const [addingToSubsonic, setAddingToSubsonic] = useState<Record<string, boolean>>({})

  const stationNames = useMemo(() => new Map(stations.map((station) => [station.id, station.name])), [stations])

  async function resolveSubsonicAvailability(items: PlaybackHistoryItem[]) {
    const candidates = items.filter((item) => Boolean(item.yt_video_id))
    if (!candidates.length) return
    try {
      const payload = await api.resolveSubsonicSongs(candidates.map((item) => ({
        key: `song:${item.yt_video_id}`,
        title: item.title,
        artist: item.artist,
        album: item.album || undefined,
        duration_ms: item.duration_ms || undefined,
        yt_video_id: item.yt_video_id,
      })))
      setSubsonicAvailability((current) => {
        const next = { ...current }
        for (const item of candidates) {
          const resolverKey = `song:${item.yt_video_id}`
          next[item.id] = Boolean(payload.songs?.[resolverKey]?.available)
        }
        return next
      })
    } catch {
      // Availability is supplemental. Keep history usable when Subsonic is unavailable.
    }
  }

  async function addHistoryItemToSubsonic(item: PlaybackHistoryItem) {
    setAddingToSubsonic((current) => ({ ...current, [item.id]: true }))
    try {
      await api.addSongToSubsonic(item)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add track to Subsonic')
      setAddingToSubsonic((current) => ({ ...current, [item.id]: false }))
    }
  }

  function apiFilters(offset = 0): PlaybackHistoryFilters {
    return { ...appliedFilters, limit: PAGE_SIZE, offset }
  }

  async function load(nextFilters: HistoryFilterState = appliedFilters) {
    setLoading(true)
    try {
      const payload = await api.history({ ...nextFilters, limit: PAGE_SIZE, offset: 0 })
      setHistory(payload)
      void resolveSubsonicAvailability(payload.items)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load history')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void Promise.all([
      load(EMPTY_FILTERS),
      api.stations().then(setStations).catch(() => setStations([])),
    ])
  }, [])

  async function loadMore() {
    if (!history?.has_more || loadingMore) return
    setLoadingMore(true)
    try {
      const payload = await api.history(apiFilters(history.items.length))
      setHistory((current) => current ? {
        ...payload,
        offset: 0,
        items: [...current.items, ...payload.items],
      } : payload)
      void resolveSubsonicAvailability(payload.items)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load more history')
    } finally {
      setLoadingMore(false)
    }
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault()
    setAppliedFilters(filters)
    void load(filters)
  }

  function clearFilters() {
    setFilters(EMPTY_FILTERS)
    setAppliedFilters(EMPTY_FILTERS)
    void load(EMPTY_FILTERS)
  }

  const activeFilterCount = Object.values(appliedFilters).filter(Boolean).length

  return (
    <div className="history-page-v2">
      <header className="history-page-header">
        <div>
          <span className="eyebrow">Playback</span>
          <h1>History</h1>
        </div>
        <div className="history-page-count" aria-label={`${history?.total ?? 0} plays in history`}>
          <strong>{history?.total ?? 0}</strong>
          <span>{activeFilterCount ? 'matches' : 'plays'}</span>
        </div>
      </header>

      <form className="history-filter-toolbar" onSubmit={applyFilters}>
        <label className="history-field history-field-search">
          <span>Search</span>
          <input
            type="search"
            placeholder="Title, artist, or album"
            value={filters.q}
            onChange={(event) => setFilters((current) => ({ ...current, q: event.target.value }))}
          />
        </label>
        <label className="history-field">
          <span>Artist</span>
          <input value={filters.artist} onChange={(event) => setFilters((current) => ({ ...current, artist: event.target.value }))} placeholder="Any artist" />
        </label>
        <label className="history-field">
          <span>Album</span>
          <input value={filters.album} onChange={(event) => setFilters((current) => ({ ...current, album: event.target.value }))} placeholder="Any album" />
        </label>

        <label className="history-field">
          <span>Source</span>
          <select value={filters.source} onChange={(event) => setFilters((current) => ({ ...current, source: event.target.value }))}>
            <option value="">All sources</option>
            <option value="subsonic">Subsonic</option>
            <option value="ytmusic">YouTube Music</option>
          </select>
        </label>
        <label className="history-field">
          <span>Result</span>
          <select value={filters.event} onChange={(event) => setFilters((current) => ({ ...current, event: event.target.value }))}>
            <option value="">Completed + skipped</option>
            <option value="completed">Completed</option>
            <option value="skipped">Skipped</option>
          </select>
        </label>
        <label className="history-field">
          <span>Station</span>
          <select value={filters.station_id} onChange={(event) => setFilters((current) => ({ ...current, station_id: event.target.value }))}>
            <option value="">All playback</option>
            <option value="__none__">Not from a station</option>
            {stations.map((station) => <option key={station.id} value={station.id}>{station.name}</option>)}
          </select>
        </label>
        <label className="history-field history-date-field">
          <span>From</span>
          <input type="date" value={filters.date_from} onChange={(event) => setFilters((current) => ({ ...current, date_from: event.target.value }))} />
        </label>
        <label className="history-field history-date-field">
          <span>To</span>
          <input type="date" value={filters.date_to} onChange={(event) => setFilters((current) => ({ ...current, date_to: event.target.value }))} />
        </label>
        <div className="history-filter-actions">
          <button className="primary-action" type="submit" disabled={loading}>{loading ? 'Searching…' : 'Apply filters'}</button>
          <button className="history-clear-button" type="button" onClick={clearFilters} disabled={loading || (!activeFilterCount && !Object.values(filters).some(Boolean))}>Clear</button>
        </div>
      </form>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="history-log-section">
        <div className="history-list-heading">
          <h2>Playback history</h2>
          {history ? <span>Showing {history.items.length} of {history.total}</span> : null}
        </div>

        <div className="history-table" role="table" aria-label="Playback history">
          <div className="history-table-head" role="row">
            <span role="columnheader">Track</span>
            <span role="columnheader">Source</span>
            <span role="columnheader">Result</span>
            <span role="columnheader">Time</span>
            <span role="columnheader">Played</span>
            <span role="columnheader">Station</span>
            <span role="columnheader">Actions</span>
          </div>

          <div className="history-table-body">
            {(history?.items ?? []).map((item) => {
              const alreadyInSubsonic = item.source?.toLowerCase() === 'subsonic' || Boolean(item.subsonic_song_id) || subsonicAvailability[item.id] === true
              return (
                <article className="history-table-row" key={item.id} role="row">
                  <div className="history-track-cell" role="cell">
                    <Artwork src={item.art_url} alt={item.title} size="sm" />
                    <div>
                      <strong>{item.title}</strong>
                      <span>{item.artist}{item.album ? ` · ${item.album}` : ''}</span>
                    </div>
                  </div>
                  <span className="history-source-badge" role="cell">{sourceLabel(item.source)}</span>
                  <span className="history-result-cell" role="cell">{resultLabel(item)}</span>
                  <span className="history-duration-cell" role="cell">{durationLabel(item)}</span>
                  <span className="history-date-cell" role="cell">{dateLabel(item.created_at)}</span>
                  <span className="history-station-cell" role="cell">{item.station_id ? stationNames.get(item.station_id) ?? 'Station' : '—'}</span>
                  <div className="history-row-actions-v2" role="cell">
                    <button className="history-icon-action" type="button" title="Play" aria-label={`Play ${item.title}`} onClick={() => player.run(() => api.playSong(item), 'play')}>
                      <PlayIcon />
                      <span>Play</span>
                    </button>
                    <button className="history-icon-action" type="button" title="Add to queue" aria-label={`Add ${item.title} to queue`} onClick={() => player.run(() => api.queueSong(item))}>
                      <QueueIcon />
                      <span>Add to queue</span>
                    </button>
                    {!alreadyInSubsonic && item.yt_video_id ? (
                      <button
                        className="history-icon-action history-import-action"
                        type="button"
                        title="Add to Subsonic"
                        aria-label={`Add ${item.title} to Subsonic`}
                        disabled={Boolean(addingToSubsonic[item.id])}
                        onClick={() => void addHistoryItemToSubsonic(item)}
                      >
                        <ImportIcon />
                        <span>{addingToSubsonic[item.id] ? 'Adding…' : 'Add to Subsonic'}</span>
                      </button>
                    ) : null}
                  </div>
                </article>
              )
            })}
          </div>
        </div>

        {history && history.items.length === 0 && !loading ? <p className="history-empty-state">No history matches these filters.</p> : null}
        {history?.has_more ? (
          <div className="history-load-more">
            <button onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? 'Loading…' : `Load more (${history.total - history.items.length} remaining)`}</button>
          </div>
        ) : null}
      </section>
    </div>
  )
}
