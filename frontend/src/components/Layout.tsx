import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { PlaybackBar } from './PlaybackBar'
import { QueuePanel } from './QueuePanel'
import { Sidebar } from './navigation/Sidebar'
import { usePlayer } from '../hooks/usePlayer'
import { useAuth } from '../auth'
import { ImportQueuedToast } from './ImportQueuedToast'

export function Layout() {
  const player = usePlayer()
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const isBigPicture = location.pathname === '/big-picture'

  async function logout() {
    await auth.logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className={`app-shell ${isBigPicture ? 'app-shell-big-picture' : 'app-shell-with-sidebar'}`}>
      {!isBigPicture ? <Sidebar user={auth.user} onLogout={() => void logout()} /> : null}
      <div className="app-main-area">
        {isBigPicture ? (
          <main className="big-picture-route-shell">
            {player.error ? <div className="error-banner big-picture-layout-error">{player.error}</div> : null}
            <Outlet context={player} />
          </main>
        ) : (
          <main className="main-grid dashboard-grid">
            <section className="content-card dashboard-content-card">
              {player.error ? <div className="error-banner">{player.error}</div> : null}
              <Outlet context={player} />
            </section>
            <QueuePanel player={player.player} refresh={player.refresh} run={player.run} />
          </main>
        )}
      </div>
      <ImportQueuedToast />
      <PlaybackBar player={player.player} audioIntent={player.audioIntent} run={player.run} setPlayer={player.setPlayer} setError={player.setError} />
    </div>
  )
}
