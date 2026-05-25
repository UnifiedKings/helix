import { NavLink, Outlet } from 'react-router-dom'
import { PlaybackBar } from './PlaybackBar'
import { QueuePanel } from './QueuePanel'
import { usePlayer } from '../hooks/usePlayer'

function SidebarLink({ to, label, icon }: { to: string; label: string; icon: string }) {
  return (
    <NavLink to={to} className="side-link">
      <span className="side-icon" aria-hidden="true">{icon}</span>
      <span>{label}</span>
    </NavLink>
  )
}

function SidebarPlaceholder({ label, icon }: { label: string; icon: string }) {
  return (
    <button className="side-link side-link-placeholder" type="button" disabled title="Placeholder for future Helix functionality">
      <span className="side-icon" aria-hidden="true">{icon}</span>
      <span>{label}</span>
    </button>
  )
}

export function Layout() {
  const player = usePlayer()

  return (
    <div className="app-shell app-shell-with-sidebar">
      <aside className="app-sidebar">
        <NavLink to="/" className="sidebar-brand" aria-label="Helix home">
          <img src="/helix-logo.png" alt="" />
          <span>Helix</span>
        </NavLink>

        <nav className="side-nav" aria-label="Main navigation">
          <SidebarLink to="/" label="Home" icon="⌂" />
          <SidebarLink to="/" label="Search" icon="⌕" />
          <SidebarLink to="/stations" label="Stations" icon="◉" />
          <SidebarLink to="/playlists" label="Playlists" icon="♫" />
          <SidebarPlaceholder label="Liked Songs" icon="♡" />
          <SidebarLink to="/history" label="History" icon="◷" />
          <SidebarLink to="/settings" label="Settings" icon="⚙" />
        </nav>

        <div className="sidebar-footer">
          <strong>Feel the music.</strong>
          <span>helix.local</span>
        </div>
      </aside>

      <div className="app-main-area">
        <header className="topbar topbar-redesigned topbar-minimal">
          <button className="profile-placeholder" type="button" title="Profile placeholder" aria-label="Profile placeholder">
            <span aria-hidden="true">H</span>
          </button>
        </header>

        <main className="main-grid dashboard-grid">
          <section className="content-card dashboard-content-card">
            {player.error ? <div className="error-banner">{player.error}</div> : null}
            <Outlet context={player} />
          </section>
          <QueuePanel player={player.player} refresh={player.refresh} run={player.run} />
        </main>
      </div>

      <PlaybackBar player={player.player} audioIntent={player.audioIntent} run={player.run} setPlayer={player.setPlayer} setError={player.setError} />
    </div>
  )
}
