import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, Pencil } from 'lucide-react'

interface Version {
  tag: string
  timestamp: string
  git_sha: string
  note: string
  active: boolean
}

const TH = 'px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-ink-faint'
const TD = 'px-3 py-3 align-middle'

export function VersionsPanel() {
  const [versions, setVersions] = useState<Version[]>([])
  const [switching, setSwitching] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<string | null>(null)
  const [draftNote, setDraftNote] = useState('')
  const [saving, setSaving] = useState(false)

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

  useEffect(() => {
    fetchVersions()
  }, [fetchVersions])

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
      } catch {
        /* restarting */
      }
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
      setVersions((prev) => prev.map((v) => (v.tag === tag ? { ...v, note: draftNote } : v)))
      setEditing(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="p-6 text-sm text-ink-faint">Loading versions…</div>

  return (
    <div className="pb-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-[22px] font-semibold tracking-tight">Versions &amp; Deploy</h1>
        <button
          onClick={fetchVersions}
          disabled={!!switching}
          className="flex items-center gap-1.5 rounded-lg border border-line bg-surface-hover px-3 py-1.5 text-xs text-ink-muted hover:text-ink disabled:opacity-50"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-signal-red/30 bg-signal-red/10 px-3.5 py-2.5 text-xs text-red-300">
          ⚠ {error}
        </div>
      )}
      {switching && (
        <div className="mb-3 rounded-lg border border-signal-yellow/30 bg-signal-yellow/10 px-3.5 py-2.5 text-xs text-yellow-300">
          Switching to <strong>{switching}</strong> — containers restarting…
        </div>
      )}

      {versions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line p-12 text-center text-sm text-ink-faint">
          No versions found. Run <code className="font-mono text-ink-muted">./scripts/deploy.sh</code> to create the first one.
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-line bg-surface">
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-line-soft">
                  <th className={TH}>Version</th>
                  <th className={TH}>Deployed</th>
                  <th className={TH}>Git SHA</th>
                  <th className={TH}>Description</th>
                  <th className={TH}>Status</th>
                  <th className={TH}></th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.tag} className={`border-b border-line-faint ${v.active ? 'bg-signal-green/5' : ''}`}>
                    <td className={TD}>
                      <code className="font-mono font-semibold text-ink">{v.tag}</code>
                    </td>
                    <td className={`${TD} whitespace-nowrap text-ink-muted`}>{v.timestamp || '—'}</td>
                    <td className={TD}>
                      {v.git_sha ? <code className="font-mono text-xs text-ink-faint">{v.git_sha}</code> : '—'}
                    </td>
                    <td className={`${TD} min-w-[220px]`}>
                      {editing === v.tag ? (
                        <div className="flex items-center gap-2">
                          <input
                            value={draftNote}
                            onChange={(e) => setDraftNote(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') saveNote(v.tag)
                              if (e.key === 'Escape') cancelEdit()
                            }}
                            autoFocus
                            placeholder="Describe what changed…"
                            className="flex-1 rounded-md border border-line-soft bg-base px-2 py-1.5 text-xs text-ink outline-none focus:border-signal-blue"
                          />
                          <button
                            onClick={() => saveNote(v.tag)}
                            disabled={saving}
                            className="rounded-md bg-signal-blue px-2.5 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
                          >
                            {saving ? '…' : 'Save'}
                          </button>
                          <button onClick={cancelEdit} className="px-1.5 text-ink-muted hover:text-ink">
                            ✕
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => startEdit(v)}
                          title="Click to edit"
                          className="group flex items-center gap-2 text-left"
                        >
                          {v.note ? (
                            <span className="text-ink-muted">{v.note}</span>
                          ) : (
                            <span className="text-ink-faint italic">Add description…</span>
                          )}
                          <Pencil size={11} className="text-ink-faint opacity-0 group-hover:opacity-100" />
                        </button>
                      )}
                    </td>
                    <td className={TD}>
                      {v.active ? (
                        <span className="inline-flex items-center gap-1.5 rounded-full bg-signal-green/15 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-300">
                          ● Active
                        </span>
                      ) : (
                        <span className="text-[11px] text-ink-faint">Inactive</span>
                      )}
                    </td>
                    <td className={TD}>
                      {!v.active && (
                        <button
                          onClick={() => switchVersion(v.tag)}
                          disabled={!!switching}
                          className="rounded-lg border border-line bg-surface-hover px-3 py-1.5 text-xs font-semibold text-ink-muted hover:border-signal-blue hover:text-blue-300 disabled:opacity-50"
                        >
                          {switching === v.tag ? 'Switching…' : 'Switch to this'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
