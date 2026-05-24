import { NavLink, Outlet } from 'react-router-dom'
import { PlaybackBar } from './PlaybackBar'
import { QueuePanel } from './QueuePanel'
import { usePlayer } from '../hooks/usePlayer'

export function Layout() {
  const player = usePlayer()

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">Helix</div>
        <nav>
          <NavLink to="/">Search</NavLink>
          <NavLink to="/stations">Stations</NavLink>
          <NavLink to="/playlists">Playlists</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
      </header>

      <main className="main-grid">
        <section className="content-card">
          {player.error ? <div className="error-banner">{player.error}</div> : null}
          <Outlet context={player} />
        </section>
        <QueuePanel player={player.player} refresh={player.refresh} run={player.run} />
      </main>

      <PlaybackBar player={player.player} audioIntent={player.audioIntent} run={player.run} setPlayer={player.setPlayer} setError={player.setError} />
    </div>
  )
}
