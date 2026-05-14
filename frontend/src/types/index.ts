// ─── Domain Types ─────────────────────────────────────────────────────────────

export type Direction = 'BUY' | 'SELL' | 'SHORT' | 'COVER'
export type OrderStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXECUTED' | 'FAILED'

export interface StagedOrder {
  id: string
  symbol: string
  direction: Direction
  quantity: number
  limit_price: number
  confidence: number     // 0.0 – 1.0
  reasoning: string
  strategy_name: string
  model_used: string
  status: OrderStatus
  trader_comment?: string
  ibkr_order_id?: number
  created_at: number     // Unix ms
  updated_at: number
}

// ─── WebSocket Message Types ──────────────────────────────────────────────────

export type WsMessageType =
  | 'order_staged'
  | 'order_approved'
  | 'order_rejected'
  | 'order_executed'
  | 'order_failed'
  | 'position_opened'
  | 'position_closed'
  | 'auto_execute_changed'
  | 'llm_unreachable'

export interface WsMessage {
  type: WsMessageType
  payload: unknown
}

// ─── Positions ────────────────────────────────────────────────────────────────

export type PositionStatus = 'OPEN' | 'CLOSED'

export interface Position {
  id: string
  symbol: string
  direction: 'LONG' | 'SHORT'
  quantity: number
  entry_price: number
  entry_time: number
  confidence: number
  alpaca_order_id?: string
  status: PositionStatus
  exit_price?: number
  exit_time?: number
  realized_pnl?: number
  stop_loss_price?: number
  take_profit_price?: number
  close_reason?: string
  created_at: number
  updated_at: number
}

// Alpaca live data (comes via the /api/alpaca/* proxy)
export interface AlpacaAccount {
  account_number: string
  status: string
  equity: string
  buying_power: string
  portfolio_value: string
  last_equity: string
  cash: string
}

export interface AlpacaPosition {
  symbol: string
  qty: string
  side: string
  avg_entry_price: string
  current_price: string
  unrealized_pl: string
  unrealized_plpc: string
  market_value: string
}

export interface TradingLimits {
  date: string
  realized_pnl: number
  trade_count: number
  is_halted: boolean
}

// ─── WebSocket Message Types ──────────────────────────────────────────────────

// ─── API Response Types ───────────────────────────────────────────────────────

export interface ListPendingResponse {
  orders: StagedOrder[]
  total: number
}

export interface GreenLightResponse {
  success: boolean
  signal_id: string
  message?: string
}
