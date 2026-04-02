import { useState } from 'react'
import { useWebSocket, useApi } from './hooks/useEngine'
import MetricsPanel from './components/MetricsPanel'
import FeedPanel from './components/FeedPanel'
import ApprovalsPanel from './components/ApprovalsPanel'
import EpisodesPanel from './components/EpisodesPanel'
import StatusBar from './components/StatusBar'

type Tab = 'feed' | 'episodes'

export default function App() {
  const { events, connected } = useWebSocket(
    `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/events`
  )
  const { data: status, refetch: refetchStatus } = useApi<any>('/api/status', 5000)
  const { data: metricsData } = useApi<any>('/api/metrics/latest', 8000)
  const { data: approvalsData, refetch: refetchApprovals } = useApi<any>('/api/approvals', 3000)
  const [tab, setTab] = useState<Tab>('feed')
  const pendingCount = approvalsData?.pending?.length ?? 0

  return (
    <div className="flex flex-col h-screen bg-[#0a0a0f] text-[#e2e2f0] overflow-hidden">
      <header className="flex items-center justify-between px-5 py-3 border-b border-[#1e1e2e] shrink-0">
        <div className="flex items-center gap-3">
          <span className="mono font-semibold text-[#6366f1] tracking-widest text-sm">AXONOPS</span>
          <span className="text-[#4a4a6a] text-xs mono">neural infrastructure engine</span>
        </div>
        <StatusBar connected={connected} status={status} />
      </header>
      <div className="flex flex-1 overflow-hidden">
        <aside className="w-64 shrink-0 border-r border-[#1e1e2e] overflow-y-auto">
          <MetricsPanel data={metricsData} />
        </aside>
        <main className="flex-1 flex flex-col overflow-hidden">
          <div className="flex items-center gap-1 px-4 py-2 border-b border-[#1e1e2e] shrink-0">
            <TabBtn active={tab === 'feed'} onClick={() => setTab('feed')}>live feed</TabBtn>
            <TabBtn active={tab === 'episodes'} onClick={() => setTab('episodes')}>episode history</TabBtn>
          </div>
          <div className="flex-1 overflow-y-auto">
            {tab === 'feed' ? <FeedPanel events={events} /> : <EpisodesPanel />}
          </div>
        </main>
        <aside className="w-72 shrink-0 border-l border-[#1e1e2e] overflow-y-auto">
          <ApprovalsPanel
            pending={approvalsData?.pending ?? []}
            pendingCount={pendingCount}
            onAction={refetchApprovals}
          />
        </aside>
      </div>
    </div>
  )
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`px-3 py-1 text-xs mono rounded transition-all ${active ? 'bg-[#1a1a26] text-[#6366f1] border border-[#2a2a3e]' : 'text-[#8888a8] hover:text-[#e2e2f0]'}`}>
      {children}
    </button>
  )
}
