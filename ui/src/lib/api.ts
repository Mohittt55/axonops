const BASE = '/api'

export async function fetchFeed(limit = 50) {
  const r = await fetch(`${BASE}/feed?limit=${limit}`)
  return r.json()
}

export async function fetchApprovals() {
  const r = await fetch(`${BASE}/approvals`)
  return r.json()
}

export async function fetchPlugins() {
  const r = await fetch(`${BASE}/plugins`)
  return r.json()
}

export async function fetchMetrics() {
  const r = await fetch(`${BASE}/metrics`)
  return r.json()
}

export async function approveAction(id: string) {
  const r = await fetch(`${BASE}/approvals/${id}/approve`, { method: 'POST' })
  return r.json()
}

export async function rejectAction(id: string) {
  const r = await fetch(`${BASE}/approvals/${id}/reject`, { method: 'POST' })
  return r.json()
}
