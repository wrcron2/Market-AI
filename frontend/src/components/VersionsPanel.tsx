import { useEffect, useState, useCallback } from 'react'

interface Version {
  tag: string
  timestamp: string
  git_sha: string
  note: string
  active: boolean
}

export function VersionsPanel() {
  const [versions, setVersions]   = useState<Version[]>([])
  const [switching, setSwitching] = useState<string | null>(null)
  const [error, setError]         = useState<string | null>(null)
  const [loading, setLoading]     = useState(true)
  const [editing, setEditing]     = useState<string | null>(null)   // tag being edited
  const [draftNote, setDraftNote] = useState('')
  const [saving, setSaving]       = useState(false)

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
      pollUntilBack()
    } catch (e: any) {
      setError(e.message)
      setSwitching(null)
    }
  }

  const pollUntilBack = () => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch('/api/versions')
        if (res.ok) {
          const data = await res.json()
          setVersions(data.versions ?? [])
          setSwitching(null)
          clearInterval(interval)
        }
      } catch { /* restarting */ }
    }, 2000)
  }

  const startEdit = (v: Version) => {
    setEditing(v.tag)
    setDraftNote(v.note)
  }

  const cancelEdit = () => {
    setEditing(null)
    setDraftNote('')
  }

  const saveNote = async (tag: string) => {
    setSaving(true)
    try {
      const res = await fetch(`/api/versions/${tag}/note`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: draftNote }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setVersions(prev => prev.map(v => v.tag === tag ? { ...v, note: draftNote } : v))
      setEditing(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
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
              <th>Description</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {versions.map(v => (
              <tr key={v.tag} className={v.active ? 'version-active-row' : ''}>
                <td className="version-tag"><code>{v.tag}</code></td>
                <td className="version-ts">{v.timestamp || '—'}</td>
                <td className="version-sha">
                  {v.git_sha ? <code className="sha">{v.git_sha}</code> : '—'}
                </td>
                <td className="version-note">
                  {editing === v.tag ? (
                    <div className="note-edit-row">
                      <input
                        className="note-input"
                        value={draftNote}
                        onChange={e => setDraftNote(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') saveNote(v.tag)
                          if (e.key === 'Escape') cancelEdit()
                        }}
                        autoFocus
                        placeholder="Describe what changed…"
                      />
                      <button className="note-save-btn" onClick={() => saveNote(v.tag)} disabled={saving}>
                        {saving ? '…' : 'Save'}
                      </button>
                      <button className="note-cancel-btn" onClick={cancelEdit}>✕</button>
                    </div>
                  ) : (
                    <div className="note-display" onClick={() => startEdit(v)} title="Click to edit">
                      {v.note
                        ? <span className="note-text">{v.note}</span>
                        : <span className="note-placeholder">Add description…</span>}
                      <span className="note-edit-icon">✎</span>
                    </div>
                  )}
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
