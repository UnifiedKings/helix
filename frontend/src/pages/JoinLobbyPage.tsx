import { FormEvent, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'

export function JoinLobbyPage() {
  const params = useParams()
  const navigate = useNavigate()
  const [inviteCode, setInviteCode] = useState(params.inviteCode ?? '')
  const [nickname, setNickname] = useState('')
  const [joining, setJoining] = useState(false)
  const [error, setError] = useState('')

  async function join(event: FormEvent) {
    event.preventDefault()
    if (!inviteCode.trim() || !nickname.trim() || joining) return
    setJoining(true)
    setError('')
    try {
      const response = await api.joinLobby(inviteCode.trim(), nickname.trim())
      navigate(`/lobby/${encodeURIComponent(response.lobby.id)}`, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not join lobby')
    } finally {
      setJoining(false)
    }
  }

  return (
    <main className="login-page lobby-join-page">
      <form className="login-card lobby-join-card" onSubmit={join}>
        <h1>Join Lobby</h1>
        <p className="muted">Enter your nickname to join this Helix shared listening room.</p>
        {error ? <div className="error-banner">{error}</div> : null}
        <input value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} placeholder="Invite code" autoComplete="off" />
        <input value={nickname} onChange={(event) => setNickname(event.target.value)} placeholder="Nickname" autoComplete="nickname" autoFocus />
        <button className="primary" disabled={joining || !inviteCode.trim() || !nickname.trim()}>{joining ? 'Joining…' : 'Join lobby'}</button>
      </form>
    </main>
  )
}
