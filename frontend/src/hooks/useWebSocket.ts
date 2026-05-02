import { useEffect, useRef, useCallback } from 'react'
import type { WsMessage, WsMessageType } from '../types'

type Handler = (payload: unknown) => void

interface UseWebSocketOptions {
  url: string
  onMessage: Partial<Record<WsMessageType, Handler>>
  onConnect?: () => void
  onDisconnect?: () => void
}

/**
 * useWebSocket — connects to the Go backend's WebSocket hub and
 * dispatches incoming messages to typed handlers.
 *
 * Reconnects automatically with exponential back-off (max 30s).
 */
export function useWebSocket({ url, onMessage, onConnect, onDisconnect }: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectDelay = useRef(1000)
  const isMounted = useRef(true)

  // Keep ALL callbacks in refs so connect() never changes identity on re-render.
  const handlersRef = useRef(onMessage)
  handlersRef.current = onMessage
  const onConnectRef = useRef(onConnect)
  onConnectRef.current = onConnect
  const onDisconnectRef = useRef(onDisconnect)
  onDisconnectRef.current = onDisconnect

  const connect = useCallback(() => {
    if (!isMounted.current) return

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      reconnectDelay.current = 1000
      onConnectRef.current?.()
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg: WsMessage = JSON.parse(event.data as string)
        const handler = handlersRef.current[msg.type]
        handler?.(msg.payload)
      } catch (err) {
        console.error('[ws] failed to parse message', err)
      }
    }

    ws.onclose = () => {
      onDisconnectRef.current?.()
      if (!isMounted.current) return
      // Exponential back-off capped at 30s.
      const delay = reconnectDelay.current
      reconnectDelay.current = Math.min(delay * 2, 30_000)
      setTimeout(connect, delay)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [url]) // url is the only real dependency now — callbacks use stable refs

  useEffect(() => {
    isMounted.current = true
    connect()
    return () => {
      isMounted.current = false
      wsRef.current?.close()
    }
  }, [connect])

  const send = useCallback((data: unknown) => {
    wsRef.current?.send(JSON.stringify(data))
  }, [])

  return { send }
}
