import { useState, useEffect, useCallback } from 'react'
import { AlertTriangle, Bell, CheckCircle, Info, Zap, RefreshCw } from 'lucide-react'

interface Alert {
  id: number
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'INFO'
  title: string
  body: string
  created_at: number
}

const SEVERITY_CONFIG = {
  CRITICAL: {
    icon: <AlertTriangle size={16} />,
    color: '#d97b84',
    bg: '#3a262b',
    border: '#d97b8444',
    label: '🔴 CRITICAL',
  },
  HIGH: {
    icon: <Zap size={16} />,
    color: '#d9a05b',
    bg: '#38301f',
    border: '#d9a05b44',
    label: '🟠 HIGH',
  },
  MEDIUM: {
    icon: <Bell size={16} />,
    color: '#d9a05b',
    bg: '#38301f',
    border: '#d9a05b44',
    label: '🟡 MEDIUM',
  },
  INFO: {
    icon: <Info size={16} />,
    color: '#a89cee',
    bg: '#2c2848',
    border: '#a89cee44',
    label: '🔵 INFO',
  },
}

function timeAgo(ms: number): string {
  const diff = Date.now() - ms
  const m = Math.floor(diff / 60000)
  const h = Math.floor(m / 60)
  const d = Math.floor(h / 24)
  if (d > 0) return `${d}d ago`
  if (h > 0) return `${h}h ago`
  if (m > 0) return `${m}m ago`
  return 'just now'
}

export function AlertsPanel() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<Alert['severity'] | 'ALL'>('ALL')

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/alerts?limit=100')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setAlerts(data.alerts ?? [])
    } catch {
      // silently keep existing alerts
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const iv = setInterval(load, 30_000)
    return () => clearInterval(iv)
  }, [load])

  const filtered = filter === 'ALL' ? alerts : alerts.filter(a => a.severity === filter)
  const counts = {
    CRITICAL: alerts.filter(a => a.severity === 'CRITICAL').length,
    HIGH: alerts.filter(a => a.severity === 'HIGH').length,
    MEDIUM: alerts.filter(a => a.severity === 'MEDIUM').length,
    INFO: alerts.filter(a => a.severity === 'INFO').length,
  }

  return (
    <div style={{ padding: '0 0 32px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Bell size={18} style={{ color: '#a89cee' }} />
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#e9e9ed' }}>
            Alerts
          </h2>
          {alerts.length > 0 && (
            <span style={{
              background: '#31333d', color: '#9397ab',
              borderRadius: 10, padding: '2px 8px', fontSize: 11, fontWeight: 600,
            }}>{alerts.length}</span>
          )}
        </div>
        <button onClick={load} style={{
          background: 'transparent', border: '1px solid #3f424d',
          color: '#9397ab', borderRadius: 6, padding: '4px 10px',
          cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <RefreshCw size={11} /> Refresh
        </button>
      </div>

      {/* Summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 20 }}>
        {(['CRITICAL','HIGH','MEDIUM','INFO'] as const).map(s => {
          const cfg = SEVERITY_CONFIG[s]
          return (
            <div
              key={s}
              onClick={() => setFilter(filter === s ? 'ALL' : s)}
              style={{
                background: filter === s ? cfg.bg : '#31333d',
                border: `1px solid ${filter === s ? cfg.color : '#31333d'}`,
                borderRadius: 10, padding: '12px 14px', cursor: 'pointer',
                transition: 'all .15s',
              }}
            >
              <div style={{ color: cfg.color, fontSize: 20, fontWeight: 800 }}>{counts[s]}</div>
              <div style={{ color: '#75798c', fontSize: 11, marginTop: 2 }}>{s}</div>
            </div>
          )
        })}
      </div>

      {/* Alert list */}
      {loading && <p style={{ color: '#75798c', fontSize: 14 }}>Loading…</p>}

      {!loading && filtered.length === 0 && (
        <div style={{
          textAlign: 'center', padding: '48px 24px',
          background: '#31333d', borderRadius: 12,
          border: '1px solid #31333d',
        }}>
          <CheckCircle size={36} style={{ color: '#b5abfc', marginBottom: 12 }} />
          <p style={{ color: '#9397ab', margin: 0, fontSize: 14 }}>
            {filter === 'ALL' ? 'No alerts yet — system is healthy.' : `No ${filter} alerts.`}
          </p>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {filtered.map(alert => {
          const cfg = SEVERITY_CONFIG[alert.severity]
          return (
            <div key={alert.id} style={{
              background: cfg.bg,
              border: `1px solid ${cfg.border}`,
              borderRadius: 12,
              padding: '14px 18px',
              position: 'relative',
              overflow: 'hidden',
            }}>
              {/* Glow bar */}
              <div style={{
                position: 'absolute', left: 0, top: 0, bottom: 0,
                width: 3, background: cfg.color, borderRadius: '12px 0 0 12px',
              }} />

              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 6 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ color: cfg.color }}>{cfg.icon}</span>
                  <span style={{ color: '#e9e9ed', fontWeight: 700, fontSize: 14 }}>{alert.title}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  <span style={{
                    background: cfg.color + '22', color: cfg.color,
                    border: `1px solid ${cfg.color}55`,
                    borderRadius: 6, padding: '2px 7px', fontSize: 10, fontWeight: 700,
                  }}>{alert.severity}</span>
                  <span style={{ color: '#75798c', fontSize: 11 }}>{timeAgo(alert.created_at)}</span>
                </div>
              </div>

              {alert.body && (
                <p style={{
                  margin: 0, color: '#9397ab', fontSize: 12, lineHeight: 1.6,
                  whiteSpace: 'pre-line',
                }}>{alert.body}</p>
              )}
            </div>
          )
        })}
      </div>

      {/* Email config reminder */}
      <div style={{
        marginTop: 24, background: '#31333d', borderRadius: 10,
        padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10,
        border: '1px solid #3f424d',
      }}>
        <Bell size={14} style={{ color: '#a89cee', flexShrink: 0 }} />
        <span style={{ fontSize: 12, color: '#75798c' }}>
          Email alerts sent to <span style={{ color: '#9397ab', fontWeight: 600 }}>
{'wrcron1@gmail.com'}
          </span>
          &nbsp;— configure via <code style={{ color: '#d97b84' }}>ALERT_EMAIL_TO</code> env var.
        </span>
      </div>
    </div>
  )
}
