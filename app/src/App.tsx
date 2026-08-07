/** Sara App — the glance over seven rooms plus the register.
 *
 * The glance (hero + tiles + Next) is always up; one room opens at a time
 * under the tab bar, hash-routed so links land where they point
 * (`#register?account=…`, `#activity?q=…`). ⌘K opens the palette; the
 * owner lens re-slices the rooms that know how. Rooms fetch lazily and
 * cache; write actions invalidate and the freshness poller closes the loop
 * once the background regeneration lands.
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { api } from './api'
import { Hero } from './components/Glance'
import { Palette } from './components/Palette'
import { TabBar } from './components/TabBar'
import { LoadError } from './components/ui'
import { isRefreshing, onRefreshChange } from './refresh'
import { ActivityRoom } from './rooms/Activity'
import { AutopilotRoom } from './rooms/Autopilot'
import { ConnectionsRoom } from './rooms/Connections'
import { GoalsRoom } from './rooms/Goals'
import { InvestmentsRoom } from './rooms/Investments'
import { MoneyMapRoom } from './rooms/MoneyMap'
import { RegisterRoom } from './rooms/Register'
import { SpendingRoom } from './rooms/Spending'
import { useFetch } from './useFetch'

const ROOM_IDS = ['spending', 'activity', 'map', 'investments', 'goals',
  'autopilot', 'connections', 'register'] as const
type RoomId = (typeof ROOM_IDS)[number]

const TABS: { id: RoomId; label: string }[] = [
  { id: 'spending', label: 'Spending' },
  { id: 'activity', label: 'Activity' },
  { id: 'map', label: 'Money map' },
  { id: 'investments', label: 'Investments' },
  { id: 'goals', label: 'Goals' },
  { id: 'autopilot', label: 'Autopilot' },
  { id: 'connections', label: 'Connections' },
]

interface Route {
  room: RoomId
  params: URLSearchParams
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#/, '')
  const [path, query = ''] = raw.split('?')
  const room = (ROOM_IDS as readonly string[]).includes(path ?? '')
    ? (path as RoomId)
    : 'spending'
  return { room, params: new URLSearchParams(query) }
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const glance = useFetch('glance', api.glance)
  const refreshing = useSyncExternalStore(onRefreshChange, isRefreshing)

  useEffect(() => {
    const onHash = () => setRoute(parseHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const go = useCallback((hash: string) => {
    setPaletteOpen(false)
    if (`#${window.location.hash.replace(/^#/, '')}` === hash) {
      setRoute(parseHash())
      return
    }
    window.location.hash = hash
  }, [])

  const pick = (id: string) => go(`#${id}`)
  const openRegister = useCallback(
    (account: string) => go(`#register?account=${encodeURIComponent(account)}`),
    [go],
  )

  const { room, params } = route
  const activeTab = room === 'register' ? 'map' : room
  const registerAccount = params.get('account') ?? ''
  const activityQ = params.get('q') ?? undefined

  return (
    <>
      <Hero data={glance.data} refreshing={refreshing}
        onPalette={() => setPaletteOpen(true)} />
      <TabBar rooms={TABS} active={activeTab} onPick={pick} />
      <main className="wrap">
        {glance.error && <LoadError error={glance.error} retry={glance.reload} />}
        <div className="room" role="tabpanel" id={`room-${activeTab}`}>
          {room === 'spending' && <SpendingRoom />}
          {room === 'activity' && <ActivityRoom key={activityQ ?? ''} initialQ={activityQ} />}
          {room === 'map' && <MoneyMapRoom onRegister={openRegister} />}
          {room === 'register' && (
            <RegisterRoom account={registerAccount} onBack={() => pick('map')} />
          )}
          {room === 'investments' && <InvestmentsRoom />}
          {room === 'goals' && <GoalsRoom />}
          {room === 'autopilot' && <AutopilotRoom onGoActivity={() => pick('activity')} />}
          {room === 'connections' && <ConnectionsRoom />}
        </div>
        <footer>
          <p>
            Everything on this page came from your vault on this machine, assembled by the same
            audited builders behind the reports — the server binds to 127.0.0.1 and nothing leaves.
          </p>
          <p>
            Whole dollars; ≈ marks estimates; every figure names its window. The static pages
            stay: <code>dashboard.sh --home</code> prints the morning page, fava is the microscope.
          </p>
        </footer>
      </main>
      {paletteOpen && (
        <Palette
          rooms={TABS}
          onClose={() => setPaletteOpen(false)}
          onGo={go}
        />
      )}
    </>
  )
}
