import { FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { LobbyPermissions, LobbyState } from '../api/types'

const DEFAULT_GUEST_PERMISSIONS: LobbyPermissions = {
  can_add_to_queue: false,
  can_remove_own_queue_items: true,
  can_remove_any_queue_item: false,
  can_control_playback: false,
  can_skip: false,
  can_seek: false,
}

function inviteUrl(lobby: LobbyState) {
  const code = lobby.invite_code || ''
  if (!code) return ''
  return `${window.location.origin}/join/${encodeURIComponent(code)}`
}

function activeMemberCount(lobby: LobbyState) {
  return (lobby.members ?? []).filter((member) => member.is_active).length
}

function LobbyArtwork({ lobby }: { lobby: LobbyState }) {
  const artwork = (lobby.queue ?? [])
    .map((item) => item.art_url)
    .filter((value): value is string => Boolean(value))
    .slice(0, 4)

  if (!artwork.length) {
    return (
      <div className="lobby-list-art lobby-list-art-empty" aria-hidden="true">
        <span>◎</span>
      </div>
    )
  }

  return (
    <div className={`lobby-list-art lobby-list-art-${Math.min(artwork.length, 4)}`} aria-hidden="true">
      {artwork.map((url, index) => (
        <img key={`${url}-${index}`} src={url} alt="" />
      ))}
    </div>
  )
}

export function LobbiesPage() {
  const [lobbies, setLobbies] = useState<LobbyState[]>([])
  const [name, setName] = useState('Shared Lobby')
  const [guestCanAdd, setGuestCanAdd] = useState(false)
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [autoCopyInvite, setAutoCopyInvite] = useState(false)

  async function load() {
    setLoading(true)
    setError('')
    try {
      const response = await api.lobbies()
      setLobbies(response.lobbies ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load lobbies')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    void api.userSettings().then((prefs) => {
      setName(prefs.settings.lobbies_default_name || 'Shared Lobby')
      setGuestCanAdd(Boolean(prefs.settings.lobbies_default_guests_can_add))
      setAutoCopyInvite(Boolean(prefs.settings.lobbies_auto_copy_invite))
    }).catch(() => {
      // Keep the bundled lobby defaults if user preferences cannot be loaded.
    })
  }, [])

  async function create(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || creating) return
    setCreating(true)
    setError('')
    setStatus('')
    try {
      const perms = { ...DEFAULT_GUEST_PERMISSIONS, can_add_to_queue: guestCanAdd }
      const lobby = await api.createLobby(name.trim(), perms, undefined, password)
      setLobbies((existing) => [lobby, ...existing.filter((item) => item.id !== lobby.id)])
      setPassword('')

      if (autoCopyInvite && lobby.invite_code) {
        const url = inviteUrl(lobby)
        if (url) {
          try {
            await navigator.clipboard.writeText(url)
            setStatus(`Created ${lobby.name}. Invite link copied.`)
          } catch {
            setStatus(`Created lobby: ${lobby.name}`)
          }
        }
      } else {
        setStatus(`Created lobby: ${lobby.name}`)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create lobby')
    } finally {
      setCreating(false)
    }
  }

  async function closeLobby(lobby: LobbyState) {
    if (!window.confirm(`Close lobby "${lobby.name}"? Guests will no longer be able to use it.`)) return
    setError('')
    try {
      await api.deleteLobby(lobby.id)
      setLobbies((existing) => existing.filter((item) => item.id !== lobby.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not close lobby')
    }
  }

  async function copyInvite(lobby: LobbyState) {
    const url = inviteUrl(lobby)
    if (!url) return
    await navigator.clipboard.writeText(url)
    setStatus('Join link copied')
  }

  async function copyJoinCode(lobby: LobbyState) {
    if (!lobby.invite_code) return
    await navigator.clipboard.writeText(lobby.invite_code)
    setStatus('Join code copied')
  }

  return (
    <div className="page-stack lobby-page-stack lobby-list-page">
      <header className="lobby-list-header">
        <div>
          <h1>Shared Lobbies</h1>
          <p>Create a listening room where guests can join with a nickname and sync to the host-controlled queue.</p>
        </div>
        <span className="lobby-list-count">{lobbies.length} {lobbies.length === 1 ? 'lobby' : 'lobbies'}</span>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}

      <section className="lobby-list-create-section">
        <h2>Create lobby</h2>
        <form className="lobby-create-form" onSubmit={create}>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Lobby name" />
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password (optional)" autoComplete="new-password" maxLength={128} />
          <label className="lobby-checkbox">
            <input type="checkbox" checked={guestCanAdd} onChange={(event) => setGuestCanAdd(event.target.checked)} />
            <span>Guests can add to queue</span>
          </label>
          <button className="primary" disabled={creating || !name.trim()}>{creating ? 'Creating…' : 'Create lobby'}</button>
        </form>
      </section>

      <section className="lobby-list-section">
        <div className="section-heading lobby-list-heading">
          <h2>Your lobbies</h2>
          <button className="lobby-refresh-button" type="button" onClick={() => void load()} disabled={loading}>
            <span aria-hidden="true">↻</span>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {lobbies.length === 0 ? <p className="muted lobby-list-empty">No lobbies yet.</p> : null}

        <div className="lobby-card-grid">
          {lobbies.map((lobby) => (
            <article className="lobby-card" key={lobby.id}>
              <LobbyArtwork lobby={lobby} />

              <div className="lobby-card-main">
                <div className="lobby-card-copy">
                  <span className={`lobby-state-label ${lobby.is_open ? 'open' : 'closed'}`}>
                    {lobby.is_open ? 'Open' : 'Closed'}
                  </span>
                  <h3>{lobby.name}</h3>
                  <p>{activeMemberCount(lobby)} members <span>•</span> {lobby.queue.length} queued</p>
                </div>

                <div className="lobby-card-actions">
                  <Link className="button-link primary" to={`/lobby/${encodeURIComponent(lobby.id)}`}>Open</Link>
                  <button type="button" onClick={() => void copyInvite(lobby)} disabled={!lobby.invite_code}>Copy join link</button>
                  <button className="danger" type="button" onClick={() => void closeLobby(lobby)}>Close</button>
                </div>
              </div>

              {lobby.invite_code ? (
                <div className="lobby-invite-row">
                  <div>
                    <span className="lobby-invite-label">Join code {lobby.has_password ? '• password protected' : ''}</span>
                    <code className="lobby-invite-code lobby-short-code">{lobby.invite_code}</code>
                  </div>
                  <button type="button" onClick={() => void copyJoinCode(lobby)}>Copy code</button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
