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

export interface WsMessage {
  type: WsMessageType
  payload: unknown
}

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
