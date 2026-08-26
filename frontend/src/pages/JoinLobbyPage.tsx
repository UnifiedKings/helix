import { FormEvent, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'

export function JoinLobbyPage() {
  const params = useParams()
  const navigate = useNavigate()
  const [inviteCode, setInviteCode] = useState((params.inviteCode ?? '').toUpperCase())
  const [nickname, setNickname] = useState('')
  const [password, setPassword] = useState('')
  const [joining, setJoining] = useState(false)
  const [checkingSavedInvite, setCheckingSavedInvite] = useState(Boolean(params.inviteCode))
  const [error, setError] = useState('')

  useEffect(() => {
    const invite = (params.inviteCode ?? '').trim().toUpperCase()
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
    if (inviteCode.trim().length !== 5 || !nickname.trim() || joining) return
    setJoining(true)
    setError('')
    try {
      const response = await api.joinLobby(inviteCode.trim().toUpperCase(), nickname.trim(), password)
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
        <p className="muted">Enter the 5-letter lobby code and your nickname. Add the lobby password if one is required.</p>
        {checkingSavedInvite ? <div className="info-banner">Checking for your saved lobby session…</div> : null}
        {error ? <div className="error-banner">{error}</div> : null}
        <input value={inviteCode} onChange={(event) => setInviteCode(event.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 5))} placeholder="5-letter code" autoComplete="off" inputMode="text" maxLength={5} disabled={checkingSavedInvite || joining} />
        <input value={nickname} onChange={(event) => setNickname(event.target.value)} placeholder="Nickname" autoComplete="nickname" autoFocus disabled={checkingSavedInvite || joining} />
        <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password (if required)" autoComplete="current-password" maxLength={128} disabled={checkingSavedInvite || joining} />
        <button className="primary" disabled={checkingSavedInvite || joining || inviteCode.trim().length !== 5 || !nickname.trim()}>{joining ? 'Joining…' : checkingSavedInvite ? 'Checking…' : 'Join lobby'}</button>
      </form>
    </main>
  )
}
