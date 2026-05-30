import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { PlaybackBar } from './PlaybackBar'
import { QueuePanel } from './QueuePanel'
import { usePlayer } from '../hooks/usePlayer'
import { useAuth } from '../auth'

function SidebarLink({ to, label, icon }: { to: string; label: string; icon: string }) {
  return (
    <NavLink to={to} className="side-link">
      <span className="side-icon" aria-hidden="true">{icon}</span>
      <span>{label}</span>
    </NavLink>
  )
}

export function Layout() {
  const player = usePlayer()
  const auth = useAuth()
  const navigate = useNavigate()

  async function logout() {
    await auth.logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-shell app-shell-with-sidebar">
      <aside className="app-sidebar">
        <NavLink to="/" className="sidebar-brand" aria-label="Helix home">
          <img src="/helix-logo.png" alt="" />
          <span>Helix</span>
        </NavLink>

        <nav className="side-nav" aria-label="Main navigation">
          <SidebarLink to="/" label="Home" icon="⌂" />
          <SidebarLink to="/search" label="Search" icon="⌕" />
          <SidebarLink to="/stations" label="Stations" icon="◉" />
          <SidebarLink to="/playlists" label="Playlists" icon="♫" />
          <SidebarLink to="/history" label="History" icon="◷" />
          <SidebarLink to="/lobbies" label="Lobbies" icon="◎" />
          <SidebarLink to="/settings" label="Settings" icon="⚙" />
        </nav>

        <div className="sidebar-account-panel">
          <div className="sidebar-account-card">
            <button className="profile-placeholder sidebar-profile-avatar" type="button" title="Profile" aria-label="Profile">
              <span aria-hidden="true">{(auth.user?.username ?? 'H').slice(0, 1).toUpperCase()}</span>
            </button>
            <div className="sidebar-account-copy">
              <strong>{auth.user?.username ?? 'Helix'}</strong>
              <span>{auth.user?.is_admin ? 'Admin' : 'User'}</span>
            </div>
          </div>
          <button className="sidebar-logout-button" type="button" onClick={() => void logout()}>
            <span aria-hidden="true">↪</span>
            Log out
          </button>
        </div>
      </aside>

      <div className="app-main-area">
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
