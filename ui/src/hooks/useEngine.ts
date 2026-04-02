import { useEffect, useRef, useState, useCallback } from 'react'

export type WSEvent = {
  type: string
  ts: string
  [key: string]: any
}

export function useWebSocket(url: string) {
  const [events, setEvents]       = useState<WSEvent[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>

    const connect = () => {
      try {
        const ws = new WebSocket(url)
        wsRef.current = ws

        ws.onopen = () => setConnected(true)

        ws.onmessage = (e) => {
          try {
            const event: WSEvent = JSON.parse(e.data)
            setEvents(prev => [event, ...prev].slice(0, 200))
          } catch {}
        }

        ws.onclose = () => {
          setConnected(false)
          reconnectTimer = setTimeout(connect, 3000)
        }

        ws.onerror = () => ws.close()
      } catch {}
    }

    connect()
    return () => {
      clearTimeout(reconnectTimer)
      wsRef.current?.close()
    }
  }, [url])

  return { events, connected }
}

export function useApi<T>(path: string, interval?: number) {
  const [data, setData]     = useState<T | null>(null)
  const [loading, setLoad]  = useState(true)
  const [error, setError]   = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(path)
      if (!r.ok) throw new Error(r.statusText)
      setData(await r.json())
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoad(false)
    }
  }, [path])

  useEffect(() => {
    fetch_()
    if (interval) {
      const t = setInterval(fetch_, interval)
      return () => clearInterval(t)
    }
  }, [fetch_, interval])

  return { data, loading, error, refetch: fetch_ }
}
