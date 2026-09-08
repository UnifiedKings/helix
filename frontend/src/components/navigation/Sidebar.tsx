import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import type { User } from '../../api/types'
import { NavIcon, type IconName } from './NavIcon'

type UserProfile = {
  username: string
  display_name: string
  avatar_data_url: string
  role: string
}

const NAV_ITEMS: Array<{ to: string; label: string; icon: IconName }> = [
  { to: '/', label: 'Home', icon: 'home' },
  { to: '/search', label: 'Search', icon: 'search' },
  { to: '/stations', label: 'Stations', icon: 'stations' },
  { to: '/playlists', label: 'Playlists', icon: 'playlists' },
  { to: '/history', label: 'History', icon: 'history' },
  { to: '/lobbies', label: 'Lobbies', icon: 'lobbies' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
]

function SidebarLink({ to, label, icon }: { to: string; label: string; icon: IconName }) {
  return <NavLink to={to} className="side-link"><span className="side-icon"><NavIcon name={icon} /></span><span>{label}</span></NavLink>
}

export function Sidebar({ user, onLogout }: { user: User | null; onLogout: () => void }) {
  const [canUpgrade, setCanUpgrade] = useState(false)
  const [profile, setProfile] = useState<UserProfile | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/capabilities', { credentials: 'include' })
      .then(async res => res.ok ? res.json() : null)
      .then(payload => {
        if (!cancelled) setCanUpgrade(Boolean(payload?.features?.quality_upgrades ?? payload?.features?.subsonic_import))
      })
      .catch(() => { if (!cancelled) setCanUpgrade(false) })
    return () => { cancelled = true }
  }, [user?.id])

  useEffect(() => {
    let cancelled = false

    const loadProfile = () => {
      if (!user) {
        setProfile(null)
        return
      }
      fetch('/api/user/settings/profile', { credentials: 'include' })
        .then(async (response) => response.ok ? response.json() as Promise<UserProfile> : null)
        .then((next) => {
          if (!cancelled && next) setProfile(next)
        })
        .catch(() => {
          if (!cancelled) setProfile(null)
        })
    }

    loadProfile()
    const listener = (event: Event) => {
      const next = (event as CustomEvent<UserProfile>).detail
      if (next) setProfile(next)
    }
    window.addEventListener('helix-user-profile-updated', listener)

    return () => {
      cancelled = true
      window.removeEventListener('helix-user-profile-updated', listener)
    }
  }, [user?.id])

  const displayName = profile?.display_name?.trim() || user?.username || 'Helix'
  const avatar = profile?.avatar_data_url || ''
  const initial = displayName.slice(0, 1).toUpperCase()

  return (
    <aside className="app-sidebar">
      <NavLink to="/" className="sidebar-brand" aria-label="Helix home"><span className="sidebar-brand-logo" aria-hidden="true" /><span>Helix</span></NavLink>
      <nav className="side-nav" aria-label="Main navigation">
        {NAV_ITEMS.map((item) => <SidebarLink key={item.to} {...item} />)}
        {canUpgrade ? <SidebarLink to="/quality-upgrades" label="Quality Upgrades" icon="quality" /> : null}
        {user?.role === 'admin' ? <SidebarLink to="/admin/settings" label="Admin" icon="admin" /> : null}
      </nav>
      <div className="sidebar-account-panel"><div className="sidebar-account-card">
        <button className="profile-placeholder sidebar-profile-avatar" type="button" title="Profile" aria-label="Profile">
          {avatar ? <img src={avatar} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} /> : <span aria-hidden="true">{initial}</span>}
        </button>
        <div className="sidebar-account-copy"><strong>{displayName}</strong><span>{user?.role === 'admin' ? 'Administrator' : 'User'}</span></div>
        <button className="sidebar-account-chevron" type="button" onClick={onLogout} title="Log out" aria-label="Log out"><span aria-hidden="true">›</span></button>
      </div></div>
    </aside>
  )
}
