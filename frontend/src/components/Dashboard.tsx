import { useState, useCallback, useEffect } from 'react'
import { GreenLightPanel } from './GreenLightPanel'
import { SignalFeed, type FeedEvent } from './SignalFeed'
import { PortfolioStats, type Stats } from './PortfolioStats'
import { TradingModeToggle, useTradingMode } from './TradingModeToggle'
import { AutoExecuteToggle, useAutoExecute } from './AutoExecuteToggle'
import { AlpacaPortfolio } from './AlpacaPortfolio'
import { useWebSocket } from '../hooks/useWebSocket'
import type { StagedOrder, ListPendingResponse } from '../types'

const API_BASE = '/api'
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
const MAX_FEED_EVENTS = 100

type Tab = 'signals' | 'portfolio'

export function Dashboard() {
  const { mode, changeMode }           = useTradingMode()
  const { enabled: autoExec, toggle }  = useAutoExecute()
  const [activeTab, setActiveTab]      = useState<Tab>('signals')
  const [pendingOrders, setPendingOrders] = useState<StagedOrder[]>([])
  const [feedEvents, setFeedEvents]    = useState<FeedEvent[]>([])
  const [wsConnected, setWsConnected]  = useState(false)
  const [llmAlert, setLlmAlert]        = useState<string | null>(null)
  const [stats, setStats] = useState<Stats>({
    totalSignals: 0,
    approved: 0,
    rejected: 0,
    executed: 0,
    avgConfidence: 0,
  })

  const pushEvent = useCallback((event: FeedEvent) => {
    setFeedEvents((prev) => [event, ...prev].slice(0, MAX_FEED_EVENTS))
  }, [])

  const updateStats = useCallback((type: FeedEvent['type'], confidence?: number) => {
    setStats((prev) => {
      const next = { ...prev }
      if (type === 'staged') {
        next.totalSignals += 1
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

  useWebSocket({
    url: WS_URL,
    onConnect:    () => setWsConnected(true),
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
      position_opened: () => {
        // AlpacaPortfolio self-refreshes; this just nudges the tab indicator
        if (activeTab !== 'portfolio') setActiveTab('portfolio')
      },
      position_closed: () => { /* AlpacaPortfolio handles its own refresh */ },
      auto_execute_changed: (payload) => {
        const { enabled } = payload as { enabled: boolean }
        // Sync toggle state from server push (another session may have toggled it)
        if (enabled !== autoExec) toggle(enabled)
      },
      llm_unreachable: (payload) => {
        const { symbol, error } = payload as { symbol?: string; error?: string }
        setLlmAlert(`${symbol ? symbol + ': ' : ''}${error ?? 'Unknown LLM failure'}`)
      },
    },
  })

  useEffect(() => {
    const loadPending = async () => {
      try {
        const res = await fetch(`${API_BASE}/orders/pending`)
        if (!res.ok) return
        const data: ListPendingResponse = await res.json()
        setPendingOrders(data.orders ?? [])
      } catch { /* backend not yet up */ }
    }

    const loadStats = async () => {
      try {
        const res = await fetch(`${API_BASE}/stats`)
        if (!res.ok) return
        const data: Stats = await res.json()
        setStats(data)
      } catch { /* backend not yet up */ }
    }

    loadPending()
    loadStats()
    const interval = setInterval(loadPending, 30_000)
    return () => clearInterval(interval)
  }, [])

  const approve = useCallback(async (signalId: string, comment: string) => {
    const res = await fetch(`${API_BASE}/orders/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signal_id: signalId, comment }),
    })
    if (!res.ok) throw new Error(await res.text())
  }, [])

  const reject = useCallback(async (signalId: string, comment: string) => {
    const res = await fetch(`${API_BASE}/orders/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signal_id: signalId, comment }),
    })
    if (!res.ok) throw new Error(await res.text())
  }, [])

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <div className="header-left">
          <h1 className="logo">⚡ MarketFlow AI</h1>
          <p className="tagline">Multi-Agent Trading · Green Light Gate Active</p>
        </div>
        <div className="header-right">
          <TradingModeToggle mode={mode} onChange={changeMode} />
          <AutoExecuteToggle enabled={autoExec} onChange={toggle} />
        </div>
      </header>

      <PortfolioStats stats={stats} wsConnected={wsConnected} />

      {/* Tab bar */}
      <div className="tab-bar">
        <button
          className={`tab-btn ${activeTab === 'signals' ? 'tab-active' : ''}`}
          onClick={() => setActiveTab('signals')}
        >
          Signals
          {pendingOrders.length > 0 && (
            <span className="tab-badge">{pendingOrders.length}</span>
          )}
        </button>
        <button
          className={`tab-btn ${activeTab === 'portfolio' ? 'tab-active' : ''}`}
          onClick={() => setActiveTab('portfolio')}
        >
          Alpaca Portfolio
        </button>
      </div>

      {activeTab === 'signals' ? (
        <div className="dashboard-body">
          <GreenLightPanel orders={pendingOrders} onApprove={approve} onReject={reject} />
          <SignalFeed events={feedEvents} />
        </div>
      ) : (
        <AlpacaPortfolio
          llmAlert={llmAlert}
          onClearAlert={() => setLlmAlert(null)}
        />
      )}
    </div>
  )
}
