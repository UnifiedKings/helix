import { FormEvent, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'

export function SetupPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')

  if (auth.status === 'loading') {
    return <main className="login-page"><div className="login-card"><h1>Helix</h1><p className="muted">Checking setup status…</p></div></main>
  }

  if (auth.status === 'authenticated') {
    return <Navigate to="/" replace />
  }

  if (!auth.setupEnabled) {
    return <Navigate to="/login" replace />
  }

  async function setup(event: FormEvent) {
    event.preventDefault()
    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }

    try {
      setError('')
      await auth.setup(username, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup failed')
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={setup}>
        <h1>Set up Helix</h1>
        <p className="muted">Create the first admin user for this Helix server.</p>
        {error ? <div className="error-banner">{error}</div> : null}
        <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Username" autoComplete="username" />
        <input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" type="password" autoComplete="new-password" />
        <input value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} placeholder="Confirm password" type="password" autoComplete="new-password" />
        <button className="primary">Create admin account</button>
      </form>
    </main>
  )
}
