import { FormEvent, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'

export function LoginPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  async function login(event: FormEvent) {
    event.preventDefault()
    try {
      setError('')
      await auth.login(username, password)
      const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/'
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={login}>
        <h1>Helix</h1>
        <p className="muted">Sign in to your self-hosted music engine.</p>
        {error ? <div className="error-banner">{error}</div> : null}
        {auth.setupEnabled ? <div className="info-banner">No admin account exists yet. <Link to="/setup">Create the first user.</Link></div> : null}
        <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Username" autoComplete="username" />
        <input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" type="password" autoComplete="current-password" />
        <button className="primary">Log in</button>
      </form>
    </main>
  )
}
