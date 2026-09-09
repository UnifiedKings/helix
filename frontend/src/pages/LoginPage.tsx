import { FormEvent, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'

type OidcConfig = {
  enabled: boolean
  display_name: string
  auto_create_users: boolean
}

type LoginBackground = {
  enabled: boolean
  image_url?: string
  photographer_name?: string
  photographer_url?: string
  unsplash_url?: string
}

const AUTHENTIK_ICON_URL = '/authentik-icon.svg'

export function LoginPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [oidc, setOidc] = useState<OidcConfig | null>(null)
  const [background, setBackground] = useState<LoginBackground | null>(null)

  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/'

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const oidcError = params.get('oidc_error')
    if (oidcError) setError(oidcError)

    let cancelled = false

    fetch('/auth/oidc/config', { credentials: 'include' })
      .then(async (response) => {
        if (!response.ok) return null
        return await response.json() as OidcConfig
      })
      .then((config) => {
        if (!cancelled) setOidc(config)
      })
      .catch(() => {
        if (!cancelled) setOidc(null)
      })

    fetch('/auth/login-background', { credentials: 'include', cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) return null
        return await response.json() as LoginBackground
      })
      .then((value) => {
        if (!cancelled) setBackground(value)
      })
      .catch(() => {
        if (!cancelled) setBackground(null)
      })

    return () => {
      cancelled = true
    }
  }, [location.search])

  async function login(event: FormEvent) {
    event.preventDefault()
    try {
      setError('')
      await auth.login(username, password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    }
  }

  function loginWithOidc() {
    window.location.assign(`/auth/oidc/login?next=${encodeURIComponent(from)}`)
  }

  const showOidc = Boolean(oidc?.enabled)
  const backgroundImage = background?.enabled && background.image_url
    ? `linear-gradient(rgba(3, 6, 12, 0.74), rgba(3, 6, 12, 0.86)), url("${background.image_url}")`
    : 'linear-gradient(135deg, #0a1019 0%, #05070d 48%, #101722 100%)'

  return (
    <main
      className="login-page"
      style={{
        position: 'relative',
        overflow: 'hidden',
        backgroundColor: '#05070d',
        backgroundImage,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        backgroundRepeat: 'no-repeat',
      }}
    >
      <div
        aria-hidden="true"
        style={{
          position: 'absolute',
          inset: 0,
          background: 'linear-gradient(90deg, rgba(4,7,13,0.30), transparent 45%, rgba(4,7,13,0.22))',
          pointerEvents: 'none',
        }}
      />

      <form
        className="login-card"
        onSubmit={login}
        style={{
          position: 'relative',
          zIndex: 1,
          backdropFilter: 'blur(18px)',
          background: 'rgba(8, 12, 20, 0.90)',
          border: '1px solid rgba(255, 255, 255, 0.10)',
          borderRadius: 8,
          boxShadow: '0 24px 60px rgba(0, 0, 0, 0.42)',
        }}
      >
        <h1>Helix</h1>
        <p className="muted">Sign in to your self-hosted music engine.</p>
        {error ? <div className="error-banner">{error}</div> : null}
        {auth.setupEnabled ? <div className="info-banner">No admin account exists yet. <Link to="/setup">Create the first user.</Link></div> : null}

        <section style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'grid', gap: 6 }}>
            <div style={{ fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', opacity: 0.72 }}>
              Local account
            </div>
            <div className="muted" style={{ fontSize: 14 }}>
              Use your Helix username and password.
            </div>
          </div>

          <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Username" autoComplete="username" />
          <input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" type="password" autoComplete="current-password" />
          <button className="primary">Log in</button>
        </section>

        {showOidc ? (
          <section style={{ display: 'grid', gap: 14, marginTop: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ height: 1, flex: 1, background: 'rgba(255,255,255,0.10)' }} />
              <span className="muted" style={{ whiteSpace: 'nowrap', fontSize: 13 }}>Or continue with</span>
              <div style={{ height: 1, flex: 1, background: 'rgba(255,255,255,0.10)' }} />
            </div>

            <div
              style={{
                display: 'grid',
                gap: 10,
                padding: '14px 0 0',
                borderTop: '1px solid rgba(255, 255, 255, 0.08)',
                background: 'transparent',
              }}
            >
              <div style={{ display: 'grid', gap: 4 }}>
                <div style={{ fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', opacity: 0.72 }}>
                  Single sign-on
                </div>
                <div className="muted" style={{ fontSize: 14 }}>
                  Sign in with your {oidc?.display_name || 'authentik'} account.
                </div>
              </div>

              <button
                type="button"
                onClick={loginWithOidc}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 12,
                  width: '100%',
                  minHeight: 48,
                  padding: '0 16px',
                  borderRadius: 4,
                  border: '1px solid rgba(253, 75, 45, 0.30)',
                  background: 'rgba(17, 23, 34, 0.94)',
                  color: 'inherit',
                  cursor: 'pointer',
                  font: 'inherit',
                  fontWeight: 600,
                }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 24,
                    height: 24,
                    flexShrink: 0,
                  }}
                >
                  <img
                    src={AUTHENTIK_ICON_URL}
                    alt=""
                    width={22}
                    height={22}
                    style={{ display: 'block', width: 22, height: 22 }}
                  />
                </span>
                <span>Sign in with {oidc?.display_name || 'authentik'}</span>
              </button>
            </div>
          </section>
        ) : null}
      </form>

      {background?.enabled && background.photographer_name && background.photographer_url && background.unsplash_url ? (
        <div
          style={{
            position: 'absolute',
            left: 14,
            bottom: 10,
            zIndex: 1,
            fontSize: 11,
            color: 'rgba(255,255,255,0.58)',
            textShadow: '0 1px 3px rgba(0,0,0,0.9)',
          }}
        >
          Photo by{' '}
          <a href={background.photographer_url} target="_blank" rel="noreferrer" style={{ color: 'inherit' }}>
            {background.photographer_name}
          </a>{' '}
          on{' '}
          <a href={background.unsplash_url} target="_blank" rel="noreferrer" style={{ color: 'inherit' }}>
            Unsplash
          </a>
        </div>
      ) : null}
    </main>
  )
}
