import { useEffect, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { PlaybackBar } from './PlaybackBar'
import { QueuePanel } from './QueuePanel'
import { Sidebar } from './navigation/Sidebar'
import { usePlayer } from '../hooks/usePlayer'
import { useAuth } from '../auth'
import { ImportQueuedToast } from './ImportQueuedToast'

const COMPACT_SHELL_QUERY = '(max-width: 1180px)'

function initialPanelVisibility() {
  if (typeof window === 'undefined') return true
  return !window.matchMedia(COMPACT_SHELL_QUERY).matches
}

export function Layout() {
  const player = usePlayer()
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const isBigPicture = location.pathname === '/big-picture'
  const [navOpen, setNavOpen] = useState(initialPanelVisibility)
  const [queueOpen, setQueueOpen] = useState(initialPanelVisibility)

  useEffect(() => {
    const media = window.matchMedia(COMPACT_SHELL_QUERY)

    const applyMode = (compact: boolean) => {
      // Wide layouts keep both rails visible. Compact layouts start with both
      // drawers closed so the page itself owns the available viewport.
      setNavOpen(!compact)
      setQueueOpen(!compact)
    }

    const handleChange = (event: MediaQueryListEvent) => applyMode(event.matches)
    media.addEventListener('change', handleChange)
    return () => media.removeEventListener('change', handleChange)
  }, [])

  useEffect(() => {
    // Navigating from a compact drawer should reveal the destination rather
    // than leaving a panel covering the new page.
    if (window.matchMedia(COMPACT_SHELL_QUERY).matches) {
      setNavOpen(false)
      setQueueOpen(false)
    }
  }, [location.pathname])

  async function logout() {
    await auth.logout()
    navigate('/login', { replace: true })
  }

  const closeShellDrawers = () => {
    setNavOpen(false)
    setQueueOpen(false)
  }

  return (
    <div
      className={[
        'app-shell',
        isBigPicture ? 'app-shell-big-picture' : 'app-shell-with-sidebar',
        navOpen ? 'shell-nav-open' : 'shell-nav-closed',
        queueOpen ? 'shell-queue-open' : 'shell-queue-closed',
      ].join(' ')}
    >
      {!isBigPicture ? <Sidebar user={auth.user} onLogout={() => void logout()} /> : null}

      {!isBigPicture ? (
        <>
          <div className="shell-responsive-controls" aria-label="Layout controls">
            <button
              type="button"
              className="shell-panel-toggle shell-nav-toggle"
              aria-label={navOpen ? 'Close navigation' : 'Open navigation'}
              title={navOpen ? 'Close navigation' : 'Open navigation'}
              aria-expanded={navOpen}
              onClick={() => {
                setNavOpen((open) => !open)
                if (window.matchMedia(COMPACT_SHELL_QUERY).matches) setQueueOpen(false)
              }}
            >
              <span aria-hidden="true">☰</span>
            </button>
            <button
              type="button"
              className="shell-panel-toggle shell-queue-toggle"
              aria-label={queueOpen ? 'Close queue' : 'Open queue'}
              title={queueOpen ? 'Close queue' : 'Open queue'}
              aria-expanded={queueOpen}
              onClick={() => {
                setQueueOpen((open) => !open)
                if (window.matchMedia(COMPACT_SHELL_QUERY).matches) setNavOpen(false)
              }}
            >
              <span className="shell-queue-toggle-glyph" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
            </button>
          </div>

          {(navOpen || queueOpen) ? (
            <button
              type="button"
              className="shell-drawer-scrim"
              aria-label="Close navigation and queue panels"
              onClick={closeShellDrawers}
            />
          ) : null}
        </>
      ) : null}

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
