import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import type { User } from '../../api/types'
import { NavIcon, type IconName } from './NavIcon'

const NAV_ITEMS: Array<{ to: string; label: string; icon: IconName }> = [
  { to: '/', label: 'Home', icon: 'home' },
  { to: '/search', label: 'Search', icon: 'search' },
  { to: '/stations', label: 'Stations', icon: 'stations' },
  { to: '/playlists', label: 'Playlists', icon: 'playlists' },
  { to: '/history', label: 'History', icon: 'history' },
  { to: '/lobbies', label: 'Lobbies', icon: 'lobbies' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
]

function SidebarLink({ to, label, icon, active }: { to: string; label: string; icon: IconName; active?: boolean }) {
  return (
    <NavLink to={to} className={({ isActive }) => `side-link${active || isActive ? ' active' : ''}`}>
      <span className="side-icon"><NavIcon name={icon} /></span>
      <span>{label}</span>
    </NavLink>
  )
}

export function Sidebar({ user, onLogout }: { user: User | null; onLogout: () => void }) {
  const location = useLocation()
  const libraryContextActive = (location.pathname.startsWith('/artists/') || location.pathname.startsWith('/albums/')) && location.search.includes('source=subsonic')
  const [canUpgrade, setCanUpgrade] = useState(false)
  const [subsonicConfigured, setSubsonicConfigured] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch('/capabilities', { credentials: 'include' })
      .then(async res => res.ok ? res.json() : null)
      .then(payload => {
        if (cancelled) return
        setCanUpgrade(Boolean(payload?.features?.quality_upgrades ?? payload?.features?.subsonic_import))
        setSubsonicConfigured(Boolean(payload?.subsonic_configured))
      })
      .catch(() => { if (!cancelled) { setCanUpgrade(false); setSubsonicConfigured(false) } })
    return () => { cancelled = true }
  }, [user?.id])

  return (
    <aside className="app-sidebar">
      <NavLink to="/" className="sidebar-brand" aria-label="Helix home"><span className="sidebar-brand-logo" aria-hidden="true" /><span>Helix</span></NavLink>
      <nav className="side-nav" aria-label="Main navigation">
        <SidebarLink to="/" label="Home" icon="home" />
        {subsonicConfigured ? <SidebarLink to="/library" label="Library" icon="library" active={libraryContextActive} /> : null}
        {NAV_ITEMS.slice(1).map((item) => <SidebarLink key={item.to} {...item} />)}
        {canUpgrade ? <SidebarLink to="/quality-upgrades" label="Quality Upgrades" icon="history" /> : null}
        {user?.role === 'admin' ? <SidebarLink to="/admin/settings" label="Admin" icon="settings" /> : null}
      </nav>
      <div className="sidebar-account-panel"><div className="sidebar-account-card">
        <button className="profile-placeholder sidebar-profile-avatar" type="button" title="Profile" aria-label="Profile"><span aria-hidden="true">{(user?.username ?? 'H').slice(0, 1).toUpperCase()}</span></button>
        <div className="sidebar-account-copy"><strong>{user?.username ?? 'Helix'}</strong><span>{user?.role === 'admin' ? 'Administrator' : 'User'}</span></div>
        <button className="sidebar-account-chevron" type="button" onClick={onLogout} title="Log out" aria-label="Log out"><span aria-hidden="true">›</span></button>
      </div></div>
    </aside>
  )
}
