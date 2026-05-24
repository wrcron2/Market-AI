import { useEffect, useState, useCallback } from 'react'

interface Version {
  tag: string
  timestamp: string
  git_sha: string
  active: boolean
}

export function VersionsPanel() {
  const [versions, setVersions]     = useState<Version[]>([])
  const [switching, setSwitching]   = useState<string | null>(null)
  const [error, setError]           = useState<string | null>(null)
  const [loading, setLoading]       = useState(true)

  const fetchVersions = useCallback(async () => {
    try {
      const res = await fetch('/api/versions')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setVersions(data.versions ?? [])
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchVersions() }, [fetchVersions])

  const switchVersion = async (tag: string) => {
    if (switching) return
    setSwitching(tag)
    setError(null)
    try {
      const res = await fetch('/api/versions/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: tag }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      // Backend will restart — poll until it's back up
      pollUntilBack(tag)
    } catch (e: any) {
      setError(e.message)
      setSwitching(null)
    }
  }

  const pollUntilBack = (_tag: string) => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch('/api/versions')
        if (res.ok) {
          const data = await res.json()
          setVersions(data.versions ?? [])
          setSwitching(null)
          clearInterval(interval)
        }
      } catch {
        // backend still restarting — keep polling
      }
    }, 2000)
  }

  if (loading) return <div className="versions-panel versions-loading">Loading versions…</div>

  return (
    <div className="versions-panel">
      <div className="versions-header">
        <h2>Deployed Versions</h2>
        <button className="btn-refresh" onClick={fetchVersions} disabled={!!switching}>↻ Refresh</button>
      </div>

      {error && <div className="versions-error">⚠ {error}</div>}

      {switching && (
        <div className="versions-switching">
          Switching to <strong>{switching}</strong> — containers restarting…
        </div>
      )}

      {versions.length === 0 ? (
        <div className="versions-empty">No versions found. Run <code>./scripts/deploy.sh</code> to create the first one.</div>
      ) : (
        <table className="versions-table">
          <thead>
            <tr>
              <th>Version</th>
              <th>Deployed</th>
              <th>Git SHA</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {versions.map(v => (
              <tr key={v.tag} className={v.active ? 'version-active-row' : ''}>
                <td className="version-tag">
                  <code>{v.tag}</code>
                </td>
                <td className="version-ts">{v.timestamp || '—'}</td>
                <td className="version-sha">
                  {v.git_sha ? <code className="sha">{v.git_sha}</code> : '—'}
                </td>
                <td>
                  {v.active
                    ? <span className="badge-active">● Active</span>
                    : <span className="badge-inactive">Inactive</span>}
                </td>
                <td>
                  {!v.active && (
                    <button
                      className="btn-switch"
                      onClick={() => switchVersion(v.tag)}
                      disabled={!!switching}
                    >
                      {switching === v.tag ? 'Switching…' : 'Switch to this'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
