import { useState, useCallback, useEffect } from 'react'
import { GreenLightPanel } from './GreenLightPanel'
import { SignalFeed, type FeedEvent } from './SignalFeed'
import { PortfolioStats, type Stats } from './PortfolioStats'
import { TradingModeToggle, useTradingMode } from './TradingModeToggle'
import { AutoExecuteToggle, useAutoExecute } from './AutoExecuteToggle'
import { LLMProviderToggle, useLLMProvider } from './LLMProviderToggle'
import { AlpacaPortfolio } from './AlpacaPortfolio'
import { VersionsPanel } from './VersionsPanel'
import { AuditLog } from './AuditLog'
import { AlertsPanel } from './AlertsPanel'
import { PipelinePanel } from './PipelinePanel'
import { ReportsPanel } from './ReportsPanel'
import { useWebSocket } from '../hooks/useWebSocket'
import { useMarketStatus } from '../hooks/useMarketStatus'
import type { StagedOrder, ListPendingResponse } from '../types'
import { AppShell } from './layout/AppShell'
import { Sidebar } from './layout/Sidebar'
import { TopBar } from './layout/TopBar'
import { AskAiPanel } from './layout/AskAiPanel'
import { Card } from './ui/primitives'

const API_BASE = '/api'
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
const MAX_FEED_EVENTS = 100

export type Tab = 'signals' | 'portfolio' | 'reports' | 'alerts' | 'audit' | 'versions' | 'pipeline' | 'config'

const BREADCRUMB: Record<Tab, string> = {
  signals: 'Live Signals',
  portfolio: 'Alpaca Portfolio',
  reports: 'Strategy Reports',
  alerts: 'Alerts',
  audit: 'Audit Log',
  versions: 'Versions & Deploy',
  pipeline: 'AI Pipeline',
  config: 'Configuration',
}

const TAB_VALUES = Object.keys({
  signals: 0, portfolio: 0, reports: 0, alerts: 0, audit: 0, versions: 0, pipeline: 0, config: 0,
} satisfies Record<Tab, number>) as Tab[]

function initialTabFromUrl(): Tab {
  const requested = new URLSearchParams(window.location.search).get('tab')
  return (TAB_VALUES as string[]).includes(requested ?? '') ? (requested as Tab) : 'signals'
}

export function Dashboard() {
  const { mode, changeMode } = useTradingMode()
  const { enabled: autoExec, toggle } = useAutoExecute()
  const { provider: llmProvider, changeProvider } = useLLMProvider()
  const { isOpen: marketOpen, minutesUntilClose: marketMinutes } = useMarketStatus()
  const [activeTab, setActiveTab] = useState<Tab>(initialTabFromUrl)
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [askOpen, setAskOpen] = useState(true)
  const [pendingOrders, setPendingOrders] = useState<StagedOrder[]>([])
  const [feedEvents, setFeedEvents] = useState<FeedEvent[]>([])
  const [wsConnected, setWsConnected] = useState(false)
  const [llmAlert, setLlmAlert] = useState<string | null>(null)
  const [llmFallbackActive, setLlmFallbackActive] = useState(false)
  const [equity, setEquity] = useState<number | null>(null)
  const [eodRefreshToken, setEodRefreshToken] = useState(0)
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
        const { signal_id, auto_executed } = payload as { signal_id: string; auto_executed?: boolean }
        pushEvent({ id: signal_id, type: 'executed', timestamp: Date.now(), autoExecuted: !!auto_executed })
        updateStats('executed')
      },
      order_failed: (payload) => {
        const { signal_id, error } = payload as { signal_id: string; error: string }
        pushEvent({ id: signal_id, type: 'failed', message: error, timestamp: Date.now() })
      },
      debate_failed: (payload) => {
        const { symbol, error } = payload as { symbol: string; error: string }
        pushEvent({ id: symbol, type: 'debate_failed', message: error, timestamp: Date.now() })
      },
      position_opened: () => {
        if (activeTab !== 'portfolio') setActiveTab('portfolio')
      },
      position_closed: () => {
        /* AlpacaPortfolio handles its own refresh */
      },
      auto_execute_changed: (payload) => {
        const { enabled } = payload as { enabled: boolean }
        if (enabled !== autoExec) toggle(enabled)
      },
      llm_provider_changed: (payload) => {
        const { provider } = payload as { provider: 'aws' | 'local' }
        if (provider !== llmProvider) changeProvider(provider)
      },
      llm_unreachable: (payload) => {
        const { symbol, error } = payload as { symbol?: string; error?: string }
        setLlmAlert(`${symbol ? symbol + ': ' : ''}${error ?? 'Unknown LLM failure'}`)
      },
      llm_fallback: (payload) => {
        const { active } = payload as { active: boolean }
        setLlmFallbackActive(active)
      },
      eod_report_ready: () => {
        setEodRefreshToken((t) => t + 1)
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
      } catch {
        /* backend not yet up */
      }
    }

    const loadStats = async () => {
      try {
        const res = await fetch(`${API_BASE}/stats`)
        if (!res.ok) return
        const data: Stats = await res.json()
        setStats(data)
      } catch {
        /* backend not yet up */
      }
    }

    const loadRecentFeed = async () => {
      try {
        const res = await fetch(`${API_BASE}/orders/recent?limit=100`)
        if (!res.ok) return
        const data = await res.json()
        const orders: StagedOrder[] = data.orders ?? []
        const events: FeedEvent[] = orders.map((o) => {
          const type: FeedEvent['type'] =
            o.status === 'EXECUTED' ? 'executed' :
            o.status === 'APPROVED' ? 'approved' :
            o.status === 'REJECTED' ? 'rejected' :
            o.status === 'FAILED' ? 'failed' : 'staged'
          return { id: o.id, type, order: o, timestamp: o.updated_at }
        })
        setFeedEvents(events)
      } catch {
        /* backend not yet up */
      }
    }

    // Live portfolio value for the top-bar ticker.
    const loadEquity = async () => {
      try {
        const res = await fetch(`${API_BASE}/alpaca/account`)
        if (!res.ok) return
        const data = await res.json()
        const v = parseFloat(data.portfolio_value ?? data.equity)
        if (!Number.isNaN(v)) setEquity(v)
      } catch {
        /* backend not yet up */
      }
    }

    loadPending()
    loadStats()
    loadRecentFeed()
    loadEquity()
    const interval = setInterval(() => {
      loadPending()
      loadEquity()
    }, 30_000)
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

  // Emergency kill switch: AUTO off + cancel pending orders + log (backend).
  const halt = useCallback(async () => {
    toggle(false)
    try {
      await fetch(`${API_BASE}/halt`, { method: 'POST' }) // TODO: implement /api/halt on the Go backend
    } catch {
      /* swallow — UI already reflects AUTO off */
    }
  }, [toggle])

  // Cmd+Shift+H → HALT
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'h') {
        e.preventDefault()
        halt()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [halt])

  return (
    <AppShell
      sidebar={
        <Sidebar
          collapsed={navCollapsed}
          active={activeTab}
          pendingCount={pendingOrders.length}
          mode={mode}
          marketOpen={marketOpen}
          onNavigate={setActiveTab}
          onToggle={() => setNavCollapsed((c) => !c)}
        />
      }
      topbar={
        <TopBar
          breadcrumb={BREADCRUMB[activeTab]}
          wsConnected={wsConnected}
          autoExec={autoExec}
          mode={mode}
          portfolioValue={equity}
          marketOpen={marketOpen}
          marketMinutes={marketMinutes}
          alertCount={pendingOrders.length}
          llmDegraded={llmFallbackActive}
          onToggleNav={() => setNavCollapsed((c) => !c)}
          onToggleAsk={() => setAskOpen((o) => !o)}
          onHalt={halt}
          onAutoClick={() => setActiveTab('config')}
          onBell={() => setActiveTab('alerts')}
          onSettings={() => setActiveTab('config')}
        />
      }
      rightPanel={
        <AskAiPanel
          open={askOpen}
          onClose={() => setAskOpen(false)}
          onAsk={async ({ role, question, model }) => {
            const res = await fetch(`${API_BASE}/ask`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ role, question, model }),
            })
            if (!res.ok) throw new Error(await res.text())
            const data = await res.json()
            return data.reply
          }}
        />
      }
    >
      {activeTab === 'signals' && (
        <div className="flex flex-col gap-4">
          <PortfolioStats stats={stats} wsConnected={wsConnected} />
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.15fr_1fr]">
            <GreenLightPanel orders={pendingOrders} onApprove={approve} onReject={reject} />
            <SignalFeed events={feedEvents} />
          </div>
        </div>
      )}

      {activeTab === 'portfolio' && (
        <AlpacaPortfolio llmAlert={llmAlert} onClearAlert={() => setLlmAlert(null)} />
      )}
      {activeTab === 'reports' && <ReportsPanel eodRefreshToken={eodRefreshToken} />}
      {activeTab === 'alerts' && <AlertsPanel />}
      {activeTab === 'audit' && <AuditLog />}
      {activeTab === 'versions' && <VersionsPanel />}
      {activeTab === 'pipeline' && <PipelinePanel />}

      {activeTab === 'config' && (
        <div className="flex max-w-[680px] flex-col gap-4">
          <h1 className="text-[22px] font-semibold tracking-tight">Configuration</h1>
          <p className="-mt-2 text-[13px] text-ink-faint">
            Every change is timestamped and written to the audit trail.
          </p>
          <Card className="p-[18px]">
            <AutoExecuteToggle enabled={autoExec} onChange={toggle} />
          </Card>
          <Card className="p-[18px]">
            <div className="mb-3 text-sm font-semibold">LLM Provider</div>
            <LLMProviderToggle />
          </Card>
          <Card className="p-[18px]">
            <div className="mb-3 text-sm font-semibold">Trading Mode</div>
            <TradingModeToggle mode={mode} onChange={changeMode} />
          </Card>
        </div>
      )}
    </AppShell>
  )
}
