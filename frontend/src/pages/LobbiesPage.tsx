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

export function LobbiesPage() {
  const [lobbies, setLobbies] = useState<LobbyState[]>([])
  const [name, setName] = useState('Shared Lobby')
  const [guestCanAdd, setGuestCanAdd] = useState(false)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

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

  useEffect(() => { void load() }, [])

  async function create(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || creating) return
    setCreating(true)
    setError('')
    setStatus('')
    try {
      const perms = { ...DEFAULT_GUEST_PERMISSIONS, can_add_to_queue: guestCanAdd }
      const lobby = await api.createLobby(name.trim(), perms)
      setLobbies((existing) => [lobby, ...existing.filter((item) => item.id !== lobby.id)])
      setStatus(`Created lobby: ${lobby.name}`)
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
    setStatus('Invite link copied')
  }

  return (
    <div className="page-stack lobby-page-stack">
      <section className="lobby-hero">
        <div>
          <h1>Shared Lobbies</h1>
          <p className="muted">Create a listening room where guests can join with a nickname and sync to the host-controlled queue.</p>
        </div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <div className="info-banner">{status}</div> : null}

      <section className="panel lobby-create-panel">
        <h2>Create lobby</h2>
        <form className="lobby-create-form" onSubmit={create}>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Lobby name" />
          <label className="lobby-checkbox">
            <input type="checkbox" checked={guestCanAdd} onChange={(event) => setGuestCanAdd(event.target.checked)} />
            Guests can add to queue
          </label>
          <button className="primary" disabled={creating || !name.trim()}>{creating ? 'Creating…' : 'Create lobby'}</button>
        </form>
      </section>

      <section className="lobby-list-section">
        <div className="section-heading">
          <h2>Your lobbies</h2>
          <button type="button" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button>
        </div>
        {lobbies.length === 0 ? <p className="muted">No lobbies yet.</p> : null}
        <div className="lobby-card-grid">
          {lobbies.map((lobby) => (
            <article className="lobby-card" key={lobby.id}>
              <div>
                <span className="eyebrow">{lobby.is_open ? 'Open' : 'Closed'}</span>
                <h3>{lobby.name}</h3>
                <p className="muted">{activeMemberCount(lobby)} members • {lobby.queue.length} queued</p>
              </div>
              <div className="lobby-card-actions">
                <Link className="button-link primary" to={`/lobby/${encodeURIComponent(lobby.id)}`}>Open</Link>
                <button type="button" onClick={() => void copyInvite(lobby)} disabled={!lobby.invite_code}>Copy invite</button>
                <button className="danger" type="button" onClick={() => void closeLobby(lobby)}>Close</button>
              </div>
              {lobby.invite_code ? <code className="lobby-invite-code">{inviteUrl(lobby)}</code> : null}
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
