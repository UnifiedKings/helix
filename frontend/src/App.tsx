import { useEffect, useState, type ReactElement } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider, RedirectIfAuthed, RequireAdmin, RequireAuth } from './auth'
import { Layout } from './components/Layout'
import { UserThemeStyles } from './components/UserThemeStyles'
import { AlbumDetailPage } from './pages/AlbumDetailPage'
import { ArtistDetailPage } from './pages/ArtistDetailPage'
import { BigPicturePage } from './pages/BigPicturePage'
import { HistoryPage } from './pages/HistoryPage'
import { HomePage } from './pages/HomePage'
import { JoinLobbyPage } from './pages/JoinLobbyPage'
import { LobbyPage } from './pages/LobbyPage'
import { LobbiesPage } from './pages/LobbiesPage'
import { LoginPage } from './pages/LoginPage'
import { PlaylistEditPage } from './pages/PlaylistEditPage'
import { PlaylistsPage } from './pages/PlaylistsPage'
import { SearchPage } from './pages/SearchPage'
import { UserSettingsPage } from './pages/UserSettingsPage'
import { AdminSettingsPage } from './pages/AdminSettingsPage'
import { SetupPage } from './pages/SetupPage'
import { StationsPage } from './pages/StationsPage'
import { QualityUpgradesPage } from './pages/QualityUpgradesPage'
import { installPersistentApiCache, subscribePersistentCache } from './api/persistentCache'

installPersistentApiCache()

function CacheRefreshBoundary({
  prefixes,
  children,
}: {
  prefixes: string[]
  children: ReactElement
}) {
  const [revision, setRevision] = useState(0)
  const prefixKey = prefixes.join('|')

  useEffect(() => (
    subscribePersistentCache(prefixes, () => {
      setRevision((current) => current + 1)
    })
  ), [prefixKey])

  // Only remount the current route page. Layout, queue, playbar, player state,
  // and navigation remain mounted and unaffected.
  return <div key={revision} style={{ display: 'contents' }}>{children}</div>
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <UserThemeStyles />
        <Routes>
          <Route path="/login" element={<RedirectIfAuthed><LoginPage /></RedirectIfAuthed>} />
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/join/:inviteCode?" element={<JoinLobbyPage />} />
          <Route path="/lobby/:lobbyId" element={<LobbyPage />} />
          <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
            <Route index element={<HomePage />} />
            <Route path="big-picture" element={<BigPicturePage />} />
            <Route path="search" element={<CacheRefreshBoundary prefixes={['capabilities']}><SearchPage /></CacheRefreshBoundary>} />
            <Route path="stations" element={<CacheRefreshBoundary prefixes={['stations:', 'capabilities']}><StationsPage /></CacheRefreshBoundary>} />
            <Route path="playlists" element={<CacheRefreshBoundary prefixes={['playlists:', 'capabilities']}><PlaylistsPage /></CacheRefreshBoundary>} />
            <Route path="playlists/:playlistId" element={<CacheRefreshBoundary prefixes={['playlist:detail:', 'playlists:']}><PlaylistEditPage /></CacheRefreshBoundary>} />
            <Route path="artists/:browseId" element={<ArtistDetailPage />} />
            <Route path="albums/:browseId" element={<AlbumDetailPage />} />
            <Route path="history" element={<CacheRefreshBoundary prefixes={['history:recent', 'stations:']}><HistoryPage /></CacheRefreshBoundary>} />
            <Route path="lobbies" element={<LobbiesPage />} />
            <Route path="quality-upgrades" element={<QualityUpgradesPage />} />
            <Route path="settings" element={<CacheRefreshBoundary prefixes={['user-settings']}><UserSettingsPage /></CacheRefreshBoundary>} />
            <Route path="admin/settings" element={<RequireAdmin><AdminSettingsPage /></RequireAdmin>} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
