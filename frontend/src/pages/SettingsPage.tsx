import { useEffect, useState } from 'react'
import { api } from '../api/client'

export function SettingsPage() {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    async function load() {
      try {
        const [healthRes, settingsRes] = await Promise.all([api.health(), api.settings()])
        setHealth(healthRes)
        setSettings(settingsRes)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not load settings')
      }
    }
    void load()
  }, [])

  return (
    <div className="page-stack">
      <div>
        <h1>Settings</h1>
        <p className="muted">Read-only status view for the first React pass. Editing admin settings can be added after auth/admin flow is finalized.</p>
      </div>
      {error ? <div className="error-banner">{error}</div> : null}
      <section className="panel">
        <h2>Backend health</h2>
        <pre>{JSON.stringify(health, null, 2)}</pre>
      </section>
      <section className="panel">
        <h2>Settings</h2>
        <pre>{JSON.stringify(settings, null, 2)}</pre>
      </section>
    </div>
  )
}
