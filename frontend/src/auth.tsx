import { ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { api } from './api/client'
import type { User } from './api/types'

type AuthStatus = 'loading' | 'authenticated' | 'anonymous'

type AuthContextValue = {
  status: AuthStatus
  user: User | null
  setupEnabled: boolean
  refresh: () => Promise<void>
  login: (username: string, password: string) => Promise<void>
  setup: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<User | null>(null)
  const [setupEnabled, setSetupEnabled] = useState(false)

  const refresh = useCallback(async () => {
    setStatus('loading')
    try {
      const me = await api.me()
      setUser(me)
      setSetupEnabled(false)
      setStatus('authenticated')
    } catch {
      setUser(null)
      try {
        const setup = await api.setupEnabled()
        setSetupEnabled(Boolean(setup.enabled))
      } catch {
        setSetupEnabled(false)
      }
      setStatus('anonymous')
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const value = useMemo<AuthContextValue>(() => ({
    status,
    user,
    setupEnabled,
    refresh,
    login: async (username: string, password: string) => {
      const me = await api.login(username, password)
      setUser(me)
      setSetupEnabled(false)
      setStatus('authenticated')
    },
    setup: async (username: string, password: string) => {
      const me = await api.setup(username, password)
      setUser(me)
      setSetupEnabled(false)
      setStatus('authenticated')
    },
    logout: async () => {
      await api.logout()
      setUser(null)
      setStatus('anonymous')
      await refresh()
    },
  }), [refresh, setupEnabled, status, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used within AuthProvider')
  return value
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const location = useLocation()

  if (auth.status === 'loading') {
    return <main className="login-page"><div className="login-card"><h1>Helix</h1><p className="muted">Checking session…</p></div></main>
  }

  if (auth.status !== 'authenticated') {
    const target = auth.setupEnabled ? '/setup' : '/login'
    return <Navigate to={target} replace state={{ from: location }} />
  }

  return <>{children}</>
}

export function RedirectIfAuthed({ children }: { children: ReactNode }) {
  const auth = useAuth()

  if (auth.status === 'loading') {
    return <main className="login-page"><div className="login-card"><h1>Helix</h1><p className="muted">Checking session…</p></div></main>
  }

  if (auth.status === 'authenticated') {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const auth = useAuth()
  if (auth.status === 'loading') return <main className="login-page"><div className="login-card"><h1>Helix</h1><p className="muted">Checking permissions…</p></div></main>
  if (auth.status !== 'authenticated') return <Navigate to="/login" replace />
  if (auth.user?.role !== 'admin') return <Navigate to="/settings" replace />
  return <>{children}</>
}
