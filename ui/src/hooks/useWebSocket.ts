import { useEffect, useRef, useState } from 'react'

export interface LivePayload {
  type: string
  metrics?: Record<string, { value: number; unit: string; tags: Record<string, string> }>
  latest_decision?: Decision
  pending_approvals?: number
}

export interface Decision {
  id: string
  timestamp: string
  action_type: string
  target: string
  reason: string
  confidence: number
  tier: string
  severity: string
  reasoning: string
  status: string
  result?: string
}

export function useWebSocket(url: string) {
  const [payload, setPayload]   = useState<LivePayload | null>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen  = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        setTimeout(connect, 2000) // reconnect
      }
      ws.onmessage = (e) => {
        try { setPayload(JSON.parse(e.data)) } catch {}
      }
    }
    connect()
    return () => wsRef.current?.close()
  }, [url])

  return { payload, connected }
}
