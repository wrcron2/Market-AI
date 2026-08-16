import { useState, useCallback, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { GreenLightPanel } from './GreenLightPanel'
import { type FeedEvent } from './SignalFeed'
import { type Stats } from './PortfolioStats'
import { useTradingMode } from '../hooks/useTradingMode'
import { useAutoExecute } from '../hooks/useAutoExecute'
import { useLLMProvider } from '../hooks/useLLMProvider'
import { AlpacaPortfolio } from './AlpacaPortfolio'
import { DashboardPage } from './DashboardPage'
import { FortressPage } from './FortressPage'
import { ExecutionPage } from './ExecutionPage'
import { OpsPage } from './OpsPage'
import { RoadmapPage } from './RoadmapPage'
import { type BrainEvent } from './BrainActivityFeed'
import { useWebSocket } from '../hooks/useWebSocket'
import { useMarketStatus } from '../hooks/useMarketStatus'
import type { StagedOrder, ListPendingResponse, AlpacaAccount, AlpacaPosition, TradingLimits } from '../types'
import { AppShell } from './layout/AppShell'
import { Sidebar } from './layout/Sidebar'
import { TopBar } from './layout/TopBar'
import { AskAiPanel } from './layout/AskAiPanel'
import { KillSwitchDialog } from './layout/KillSwitchDialog'
import { PageHead } from './ui/primitives'

const API_BASE = '/api'
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
const MAX_FEED_EVENTS = 100

export type Tab = 'dashboard' | 'green-light' | 'positions' | 'fortress' | 'execution' | 'ops' | 'roadmap'

const TAB_VALUES: Tab[] = ['dashboard', 'green-light', 'positions', 'fortress', 'execution', 'ops', 'roadmap']

/** Old paths redirect to the new IA — nothing 404s. */
const REDIRECTS: Record<string, Tab> = {
  '': 'dashboard',
  signals: 'dashboard',
  portfolio: 'positions',
  reports: 'fortress',
  pipeline: 'execution',
  audit: 'execution',
  versions: 'execution',
  config: 'ops',
  alerts: 'ops',
}

function normalizePathSegment(pathname: string): string {
  return pathname.replace(/^\/+/, '').replace(/\/+$/, '')
}

function tabFromPath(pathname: string): Tab | null {
  const segment = normalizePathSegment(pathname)
  if ((TAB_VALUES as string[]).includes(segment)) return segment as Tab
  return REDIRECTS[segment] ?? null
}

export function Dashboard() {
  const { mode, changeMode } = useTradingMode()
  const { enabled: autoExec, toggle } = useAutoExecute()
  const { provider: llmProvider, changeProvider } = useLLMProvider()
  const { isOpen: marketOpen, minutesUntilClose: marketMinutes } = useMarketStatus()
  const location = useLocation()
  const navigate = useNavigate()
  // Path segment wins (old segments redirect). Otherwise fall back to a legacy
  // `?tab=` query param (old bookmarks/links), then 'dashboard'.
  const legacyTabParam = new URLSearchParams(location.search).get('tab')
  const legacyTab: Tab | null = legacyTabParam
    ? (REDIRECTS[legacyTabParam] ??
      ((TAB_VALUES as string[]).includes(legacyTabParam) ? (legacyTabParam as Tab) : null))
    : null
  const activeTab: Tab = tabFromPath(location.pathname) ?? legacyTab ?? 'dashboard'
  const setActiveTab = useCallback((tab: Tab) => navigate(`/${tab}`), [navigate])
  const [askOpen, setAskOpen] = useState(true)
  const [killOpen, setKillOpen] = useState(false)
  const [pendingOrders, setPendingOrders] = useState<StagedOrder[]>([])
  const [feedEvents, setFeedEvents] = useState<FeedEvent[]>([])
  const [brainEvents, setBrainEvents] = useState<BrainEvent[]>([])
  const [wsConnected, setWsConnected] = useState(false)
  const [llmAlert, setLlmAlert] = useState<string | null>(null)
  const [llmFallbackActive, setLlmFallbackActive] = useState(false)
  const [account, setAccount] = useState<AlpacaAccount | null>(null)
  const [alpacaPositions, setAlpacaPositions] = useState<AlpacaPosition[]>([])
  const [limits, setLimits] = useState<TradingLimits | null>(null)
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
      brain_activity: (payload) => {
        const ev = payload as BrainEvent
        setBrainEvents((prev) => [ev, ...prev].slice(0, 120))
      },
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
        if (activeTab !== 'positions') setActiveTab('positions')
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

  // Canonicalize "/", old paths, a trailing slash, or a legacy "?tab=" link to
  // the matching new route — preserving any other query params.
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    let needsRewrite = false

    if (params.has('tab')) {
      params.delete('tab')
      needsRewrite = true
    }

    const canonicalPath = `/${activeTab}`
    if (location.pathname !== canonicalPath) {
      needsRewrite = true
    }

    if (needsRewrite) {
      const query = params.toString()
      navigate(`${canonicalPath}${query ? `?${query}` : ''}`, { replace: true })
    }
  }, [location.pathname, location.search, activeTab, navigate])

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

    // Live Alpaca snapshot for the top bar + dashboard vitals.
    const loadAccount = async () => {
      try {
        const [acctRes, posRes, limRes] = await Promise.all([
          fetch(`${API_BASE}/alpaca/account`),
          fetch(`${API_BASE}/alpaca/positions`),
          fetch(`${API_BASE}/trading/limits`),
        ])
        if (acctRes.ok) setAccount(await acctRes.json())
        if (posRes.ok) setAlpacaPositions(await posRes.json())
        if (limRes.ok) setLimits(await limRes.json())
      } catch {
        /* backend not yet up */
      }
    }

    loadPending()
    loadStats()
    loadRecentFeed()
    loadAccount()
    const interval = setInterval(() => {
      loadPending()
      loadAccount()
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

  // Cmd+Shift+H → open kill-switch dialog
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'h') {
        e.preventDefault()
        setKillOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const portfolioValue = account ? parseFloat(account.portfolio_value ?? account.equity) : null
  const dayPnl = account
    ? parseFloat(account.equity ?? '0') - parseFloat(account.last_equity ?? '0')
    : null
  const grossExposure = alpacaPositions.reduce(
    (s, p) => s + Math.abs(parseFloat(p.market_value || '0')),
    0,
  )

  return (
    <AppShell
      sidebar={
        <Sidebar
          active={activeTab}
          pendingCount={pendingOrders.length}
          onNavigate={setActiveTab}
        />
      }
      topbar={
        <TopBar
          wsConnected={wsConnected}
          autoExec={autoExec}
          mode={mode}
          portfolioValue={portfolioValue != null && !Number.isNaN(portfolioValue) ? portfolioValue : null}
          dayPnl={dayPnl != null && !Number.isNaN(dayPnl) ? dayPnl : null}
          marketOpen={marketOpen}
          marketMinutes={marketMinutes}
          alertCount={pendingOrders.length}
          llmDegraded={llmFallbackActive}
          onToggleAsk={() => setAskOpen((o) => !o)}
          onBell={() => setActiveTab('ops')}
          onKill={() => setKillOpen(true)}
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
      {activeTab === 'dashboard' && (
        <DashboardPage
          account={account}
          positions={alpacaPositions}
          limits={limits}
          stats={stats}
          pendingOrders={pendingOrders}
          feedEvents={feedEvents}
          brainEvents={brainEvents}
          marketOpen={marketOpen}
          modeLabel={mode === 'ibkr' ? 'Alpaca paper' : 'Yahoo sim'}
          onNavigate={setActiveTab}
        />
      )}

      {activeTab === 'green-light' && (
        <GreenLightPanel orders={pendingOrders} onApprove={approve} onReject={reject} />
      )}

      {activeTab === 'positions' && (
        <div className="flex flex-col gap-[22px]">
          <PageHead
            eyebrow="Positions"
            title={`${alpacaPositions.length} open · Alpaca paper`}
          />
          <AlpacaPortfolio llmAlert={llmAlert} onClearAlert={() => setLlmAlert(null)} />
        </div>
      )}

      {activeTab === 'fortress' && <FortressPage eodRefreshToken={eodRefreshToken} />}
      {activeTab === 'execution' && <ExecutionPage brainEvents={brainEvents} />}
      {activeTab === 'ops' && (
        <OpsPage mode={mode} onModeChange={changeMode} autoExec={autoExec} onAutoExecChange={toggle} />
      )}
      {activeTab === 'roadmap' && <RoadmapPage />}

      <KillSwitchDialog
        open={killOpen}
        onClose={() => setKillOpen(false)}
        onConfirm={halt}
        positionsCount={account ? alpacaPositions.length : null}
        grossExposure={account ? grossExposure : null}
      />
    </AppShell>
  )
}
