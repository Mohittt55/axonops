import { useEffect, useRef, useState, useCallback } from 'react'

export interface FeedEntry {
  id: string
  timestamp: string
  status: 'ok' | 'notify' | 'pending' | 'success' | 'failed' | 'rejected'
  message: string
  metrics: Record<string, number>
}

export interface PendingDecision {
  action_id: string
  action_type: string
  target: string
  reason: string
  confidence: number
  reasoning: string
  anomaly_score: number
  severity: string
  created_at: string
}

export interface WSState {
  feed: FeedEntry[]
  pending: PendingDecision[]
  stats: { total_episodes: number; pending_count: number }
  connected: boolean
}

const WS_URL = import.meta.env.VITE_WS_URL ?? `ws://${location.host}/ws/feed`

export function useAxonOps() {
  const [state, setState] = useState<WSState>({
    feed: [], pending: [], stats: { total_episodes: 0, pending_count: 0 }, connected: false,
  })
  const ws = useRef<WebSocket | null>(null)
  const reconnect = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return

    const socket = new WebSocket(WS_URL)
    ws.current = socket

    socket.onopen = () => {
      setState(s => ({ ...s, connected: true }))
    }

    socket.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'init') {
        setState(s => ({
          ...s,
          feed:    msg.feed    ?? s.feed,
          pending: msg.pending ?? s.pending,
        }))
      } else if (msg.type === 'update') {
        setState(s => ({
          ...s,
          feed:    msg.feed    ?? s.feed,
          pending: msg.pending ?? s.pending,
          stats:   msg.stats   ?? s.stats,
        }))
      }
    }

    socket.onclose = () => {
      setState(s => ({ ...s, connected: false }))
      reconnect.current = setTimeout(connect, 3000)
    }

    socket.onerror = () => socket.close()
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnect.current)
      ws.current?.close()
    }
  }, [connect])

  const approve = useCallback(async (action_id: string) => {
    await fetch('/api/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action_id }),
    })
  }, [])

  const reject = useCallback(async (action_id: string) => {
    await fetch('/api/reject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action_id }),
    })
  }, [])

  return { ...state, approve, reject }
}
