// Customer link page (/s/:token): what the customer sees during the call.
// All state comes from useLinkSession (snapshot + call events through one reducer).
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useParams } from 'react-router'
import { CalendarView } from '../components/link/CalendarView.tsx'
import { CartView } from '../components/link/CartView.tsx'
import { ConfirmPanel } from '../components/link/ConfirmPanel.tsx'
import { Header } from '../components/link/Header.tsx'
import { ActionsTab, NotesTab } from '../components/link/Lists.tsx'
import { ExpiredScreen, InvalidScreen, LoadingScreen } from '../components/link/Screens.tsx'
import { TabBar, type TabDef, type TabId } from '../components/link/TabBar.tsx'
import { Transcript } from '../components/link/Transcript.tsx'
import { useMediaQuery } from '../lib/link/hooks.ts'
import { useLinkSession } from '../lib/link/useLinkSession.ts'

const NEUTRAL = { accent: '#3f4a45', surface: '#f7f7f5' }

export default function LinkPage() {
  const { token = '' } = useParams()
  const { state, phase, connected, brand, expireNow } = useLinkSession(token)
  const desktop = useMediaQuery('(min-width: 768px)')
  const wasLive = useRef(false)
  if (phase === 'live') wasLive.current = true

  const style = {
    '--accent': brand.theme.accent ?? NEUTRAL.accent,
    '--surface': brand.theme.surface ?? NEUTRAL.surface,
  } as CSSProperties

  useEffect(() => {
    document.title = brand.name ? `${brand.name} · Live call` : 'Live call'
  }, [brand.name])

  let body: ReactNode
  if (phase === 'invalid') body = <InvalidScreen />
  else if (phase === 'expired') body = <ExpiredScreen brand={brand.name} fade={wasLive.current} />
  else if (phase === 'loading' || !state.meta) body = <LoadingScreen />
  else
    body = (
      <LivePage
        token={token}
        state={state}
        connected={connected}
        desktop={desktop}
        fading={phase === 'expiring'}
        onExpire={expireNow}
      />
    )
  return <div style={style}>{body}</div>
}

type LiveProps = {
  token: string
  state: ReturnType<typeof useLinkSession>['state']
  connected: boolean
  desktop: boolean
  fading: boolean
  onExpire: () => void
}

function LivePage({ token, state, connected, desktop, fading, onExpire }: LiveProps) {
  const meta = state.meta!
  const agentName = meta.agent_name ?? 'your agent'
  const [tab, setTab] = useState<TabId>('live')
  const [follow, setFollow] = useState(true)
  const [seen, setSeen] = useState({ notes: 0, actions: 0 })
  const confirmVisible = state.confirmRequestedSeq != null

  // Follow along: Live view on the first state event, Confirm when confirmation is requested.
  useEffect(() => {
    if (follow && state.firstStateSeq != null) setTab('live')
  }, [follow, state.firstStateSeq])
  useEffect(() => {
    if (follow && state.confirmRequestedSeq != null) setTab('confirm')
  }, [follow, state.confirmRequestedSeq])
  useEffect(() => {
    if (desktop && tab === 'transcript') setTab('live')
  }, [desktop, tab])
  useEffect(() => {
    if (tab === 'notes' || tab === 'actions') {
      const count = tab === 'notes' ? state.notes.length : state.actions.length
      setSeen((s) => (s[tab] === count ? s : { ...s, [tab]: count }))
    }
  }, [tab, state.notes.length, state.actions.length])

  const select = (id: TabId) => {
    setFollow(false) // any manual tab click turns follow-along off
    setTab(id)
  }

  const tabs: TabDef[] = [
    { id: 'live', label: 'Live view' },
    ...(!desktop ? [{ id: 'transcript' as const, label: 'Transcript' }] : []),
    { id: 'notes', label: 'Notes', badge: state.notes.length - seen.notes },
    { id: 'actions', label: 'Agent actions', badge: state.actions.length - seen.actions },
    ...(confirmVisible ? [{ id: 'confirm' as const, label: 'Confirm', attention: true }] : []),
  ]

  const panel =
    tab === 'live' ? (
      state.cart ? (
        <CartView state={state} agentName={agentName} />
      ) : (
        <CalendarView state={state} />
      )
    ) : tab === 'transcript' ? (
      <Transcript state={state} agentName={agentName} embedded />
    ) : tab === 'notes' ? (
      <NotesTab notes={state.notes} liveFrom={state.liveFrom} agentName={agentName} />
    ) : tab === 'actions' ? (
      <ActionsTab actions={state.actions} liveFrom={state.liveFrom} agentName={agentName} />
    ) : (
      <ConfirmPanel state={state} token={token} agentName={agentName} />
    )

  const tabBar = <TabBar tabs={tabs} active={tab} onSelect={select} follow={follow} onFollowChange={setFollow} compact={!desktop} />
  const fade = `transition-opacity duration-250 ${fading ? 'opacity-0' : 'opacity-100'}`

  if (!desktop) {
    return (
      <div className={`min-h-dvh bg-white ${fade}`} data-testid="link-page" data-layout="mobile">
        <div className="sticky top-0 z-20 bg-white">
          <Header meta={meta} callStatus={state.callStatus} connected={connected} compact onExpire={onExpire} />
          {tabBar}
        </div>
        <main className="px-4 py-5" key={tab}>
          <div className="animate-fade-in">{panel}</div>
        </main>
      </div>
    )
  }

  return (
    <div className={`flex h-dvh flex-col bg-surface ${fade}`} data-testid="link-page" data-layout="desktop">
      <Header meta={meta} callStatus={state.callStatus} connected={connected} compact={false} onExpire={onExpire} />
      <div className="mx-auto grid min-h-0 w-full max-w-[1360px] flex-1 grid-cols-[3fr_2fr] gap-6 px-8 py-6">
        <section className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-black/[0.07] bg-white">
          {tabBar}
          <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6" data-testid={`panel-${tab}`}>
            <div key={tab} className="h-full animate-fade-in">
              {panel}
            </div>
          </div>
        </section>
        <aside className="min-h-0 overflow-hidden rounded-2xl border border-black/[0.07] bg-white">
          <Transcript state={state} agentName={agentName} />
        </aside>
      </div>
    </div>
  )
}
