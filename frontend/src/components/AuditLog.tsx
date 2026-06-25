import { useState, useEffect, useCallback } from 'react'

interface AuditEntry {
  id: number
  signal_id: string
  from_status: string
  to_status: string
  actor: string
  message: string
  timestamp: number
}

const STATUS_COLORS: Record<string, string> = {
  PENDING:  '#f59e0b',
  APPROVED: '#3b82f6',
  EXECUTED: '#22c55e',
  REJECTED: '#ef4444',
  FAILED:   '#ef4444',
}

function statusBadge(status: string) {
  const color = STATUS_COLORS[status] ?? '#6b7280'
  return (
    <span style={{
      background: color + '22',
      color,
      border: `1px solid ${color}55`,
      borderRadius: 4,
      padding: '1px 7px',
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: '0.04em',
    }}>
      {status}
    </span>
  )
}

export function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/orders/audit-log?limit=200')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setEntries(data.entries ?? [])
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const iv = setInterval(load, 30_000)
    return () => clearInterval(iv)
  }, [load])

  return (
    <div style={{ padding: '0 0 24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#e2e8f0' }}>
          Audit Events
        </h2>
        <button
          onClick={load}
          style={{
            background: 'transparent',
            border: '1px solid #334155',
            color: '#94a3b8',
            borderRadius: 6,
            padding: '4px 12px',
            cursor: 'pointer',
            fontSize: 12,
          }}
        >
          Refresh
        </button>
      </div>

      {loading && <p style={{ color: '#64748b', fontSize: 14 }}>Loading…</p>}
      {error   && <p style={{ color: '#ef4444', fontSize: 14 }}>Error: {error}</p>}
      {!loading && !error && entries.length === 0 && (
        <p style={{ color: '#64748b', fontSize: 14 }}>No audit events yet.</p>
      )}

      {entries.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: 12,
            color: '#cbd5e1',
          }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1e293b', color: '#64748b', textAlign: 'left' }}>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Time</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Signal ID</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Transition</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Actor</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Message</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr
                  key={e.id}
                  style={{ borderBottom: '1px solid #0f172a' }}
                >
                  <td style={{ padding: '6px 10px', whiteSpace: 'nowrap', color: '#64748b' }}>
                    {new Date(e.timestamp).toLocaleString()}
                  </td>
                  <td style={{ padding: '6px 10px', fontFamily: 'monospace', color: '#94a3b8', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {e.signal_id.slice(0, 8)}…
                  </td>
                  <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>
                    {e.from_status
                      ? <>{statusBadge(e.from_status)} <span style={{ color: '#475569', margin: '0 4px' }}>→</span> {statusBadge(e.to_status)}</>
                      : statusBadge(e.to_status)
                    }
                  </td>
                  <td style={{ padding: '6px 10px', color: '#94a3b8' }}>
                    {e.actor}
                  </td>
                  <td style={{ padding: '6px 10px', color: '#64748b', maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {e.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
