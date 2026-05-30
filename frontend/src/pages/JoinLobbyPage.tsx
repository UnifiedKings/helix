import { FormEvent, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'

export function JoinLobbyPage() {
  const params = useParams()
  const navigate = useNavigate()
  const [inviteCode, setInviteCode] = useState(params.inviteCode ?? '')
  const [nickname, setNickname] = useState('')
  const [joining, setJoining] = useState(false)
  const [checkingSavedInvite, setCheckingSavedInvite] = useState(Boolean(params.inviteCode))
  const [error, setError] = useState('')

  useEffect(() => {
    const invite = (params.inviteCode ?? '').trim()
    if (!invite) {
      setCheckingSavedInvite(false)
      return
    }

    let cancelled = false
    const saved = api.savedLobbyInvite(invite)
    if (saved?.nickname) setNickname(saved.nickname)

    async function resume() {
      setCheckingSavedInvite(true)
      try {
        const lobby = await api.resumeJoinedLobby(invite)
        if (cancelled) return
        navigate(`/lobby/${encodeURIComponent(lobby.id)}`, { replace: true })
      } catch {
        if (cancelled) return
        api.clearSavedLobbyInvite(invite)
        setCheckingSavedInvite(false)
      }
    }

    void resume()

    return () => {
      cancelled = true
    }
  }, [navigate, params.inviteCode])

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
        {checkingSavedInvite ? <div className="info-banner">Checking for your saved lobby session…</div> : null}
        {error ? <div className="error-banner">{error}</div> : null}
        <input value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} placeholder="Invite code" autoComplete="off" disabled={checkingSavedInvite || joining} />
        <input value={nickname} onChange={(event) => setNickname(event.target.value)} placeholder="Nickname" autoComplete="nickname" autoFocus disabled={checkingSavedInvite || joining} />
        <button className="primary" disabled={checkingSavedInvite || joining || !inviteCode.trim() || !nickname.trim()}>{joining ? 'Joining…' : checkingSavedInvite ? 'Checking…' : 'Join lobby'}</button>
      </form>
    </main>
  )
}
