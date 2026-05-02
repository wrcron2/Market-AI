import { useState, useCallback, useEffect } from 'react'
import { GreenLightPanel } from './GreenLightPanel'
import { SignalFeed, type FeedEvent } from './SignalFeed'
import { PortfolioStats, type Stats } from './PortfolioStats'
import { TradingModeToggle, useTradingMode } from './TradingModeToggle'
import { useWebSocket } from '../hooks/useWebSocket'
import type { StagedOrder, ListPendingResponse } from '../types'

const API_BASE = '/api'
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
const MAX_FEED_EVENTS = 100

/**
 * Dashboard — the root component.
 *
 * Responsibilities:
 * - Maintains the pending orders queue (polled + WebSocket push)
 * - Manages the live feed event log
 * - Provides Green Light / Reject actions to child components
 * - Tracks session-level KPI stats
 */
export function Dashboard() {
  const { mode, changeMode } = useTradingMode()
  const [pendingOrders, setPendingOrders] = useState<StagedOrder[]>([])
  const [feedEvents, setFeedEvents] = useState<FeedEvent[]>([])
  const [wsConnected, setWsConnected] = useState(false)
  const [stats, setStats] = useState<Stats>({
    totalSignals: 0,
    approved: 0,
    rejected: 0,
    executed: 0,
    avgConfidence: 0,
  })

  // ── Feed helper ────────────────────────────────────────────────────────────
  const pushEvent = useCallback((event: FeedEvent) => {
    setFeedEvents((prev) => [event, ...prev].slice(0, MAX_FEED_EVENTS))
  }, [])

  // ── Stats helper ───────────────────────────────────────────────────────────
  const updateStats = useCallback((type: FeedEvent['type'], confidence?: number) => {
    setStats((prev) => {
      const next = { ...prev }
      if (type === 'staged') {
        next.totalSignals += 1
        // Running average of confidence
        if (confidence !== undefined) {
          next.avgConfidence =
            (prev.avgConfidence * prev.totalSignals + confidence) / next.totalSignals
        }
      }
      if (type === 'approved') next.approved += 1
      if (type === 'rejected') next.rejected += 1
      if (type === 'executed') next.executed += 1
      return next
    })
  }, [])

  // ── WebSocket handlers ─────────────────────────────────────────────────────
  useWebSocket({
    url: WS_URL,
    onConnect: () => setWsConnected(true),
    onDisconnect: () => setWsConnected(false),
    onMessage: {
      order_staged: (payload) => {
        const order = payload as StagedOrder
        setPendingOrders((prev) => [order, ...prev])
        pushEvent({ id: order.id, type: 'staged', order, timestamp: Date.now() })
        updateStats('staged', order.confidence)
      },
      order_approved: (payload) => {
        const order = payload as StagedOrder
        setPendingOrders((prev) => prev.filter((o) => o.id !== order.id))
        pushEvent({ id: order.id, type: 'approved', order, timestamp: Date.now() })
        updateStats('approved')
      },
      order_rejected: (payload) => {
        const { signal_id } = payload as { signal_id: string }
        setPendingOrders((prev) => prev.filter((o) => o.id !== signal_id))
        pushEvent({ id: signal_id, type: 'rejected', timestamp: Date.now() })
        updateStats('rejected')
      },
      order_executed: (payload) => {
        const { signal_id } = payload as { signal_id: string }
        pushEvent({ id: signal_id, type: 'executed', timestamp: Date.now() })
        updateStats('executed')
      },
      order_failed: (payload) => {
        const { signal_id, error } = payload as { signal_id: string; error: string }
        pushEvent({ id: signal_id, type: 'failed', message: error, timestamp: Date.now() })
      },
    },
  })

  // ── Initial load of pending orders (REST poll) ─────────────────────────────
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/orders/pending`)
        if (!res.ok) return
        const data: ListPendingResponse = await res.json()
        setPendingOrders(data.orders ?? [])
      } catch {
        // Backend not yet available — WebSocket push will hydrate the list.
      }
    }
    load()
    // Re-poll every 30s as a fallback if WebSocket misses an event.
    const interval = setInterval(load, 30_000)
    return () => clearInterval(interval)
  }, [])

  // ── Green Light actions ────────────────────────────────────────────────────
  const approve = useCallback(async (signalId: string, comment: string) => {
    const res = await fetch(`${API_BASE}/orders/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signal_id: signalId, comment }),
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text)
    }
  }, [])

  const reject = useCallback(async (signalId: string, comment: string) => {
    const res = await fetch(`${API_BASE}/orders/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signal_id: signalId, comment }),
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text)
    }
  }, [])

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <div className="header-left">
          <h1 className="logo">⚡ MarketFlow AI</h1>
          <p className="tagline">Multi-Agent Trading · Green Light Gate Active</p>
        </div>
        <div className="header-right">
          <TradingModeToggle mode={mode} onChange={changeMode} />
        </div>
      </header>

      <PortfolioStats stats={stats} wsConnected={wsConnected} />

      <div className="dashboard-body">
        <GreenLightPanel
          orders={pendingOrders}
          onApprove={approve}
          onReject={reject}
        />
        <SignalFeed events={feedEvents} />
      </div>
    </div>
  )
}
