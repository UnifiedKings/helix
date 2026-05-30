import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider, RedirectIfAuthed, RequireAuth } from './auth'
import { Layout } from './components/Layout'
import { AlbumDetailPage } from './pages/AlbumDetailPage'
import { ArtistDetailPage } from './pages/ArtistDetailPage'
import { HistoryPage } from './pages/HistoryPage'
import { HomePage } from './pages/HomePage'
import { JoinLobbyPage } from './pages/JoinLobbyPage'
import { LobbyPage } from './pages/LobbyPage'
import { LobbiesPage } from './pages/LobbiesPage'
import { LoginPage } from './pages/LoginPage'
import { PlaylistEditPage } from './pages/PlaylistEditPage'
import { PlaylistsPage } from './pages/PlaylistsPage'
import { SearchPage } from './pages/SearchPage'
import { SettingsPage } from './pages/SettingsPage'
import { SetupPage } from './pages/SetupPage'
import { StationsPage } from './pages/StationsPage'

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<RedirectIfAuthed><LoginPage /></RedirectIfAuthed>} />
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/join/:inviteCode?" element={<JoinLobbyPage />} />
          <Route path="/lobby/:lobbyId" element={<LobbyPage />} />
          <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
            <Route index element={<HomePage />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="stations" element={<StationsPage />} />
            <Route path="playlists" element={<PlaylistsPage />} />
            <Route path="playlists/:playlistId" element={<PlaylistEditPage />} />
            <Route path="artists/:browseId" element={<ArtistDetailPage />} />
            <Route path="albums/:browseId" element={<AlbumDetailPage />} />
            <Route path="history" element={<HistoryPage />} />
            <Route path="lobbies" element={<LobbiesPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
