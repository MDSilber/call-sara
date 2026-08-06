/** Sara App — the glance over six rooms.
 *
 * The glance (hero + tiles + Next) is always up; one room opens at a time
 * under the tab bar, hash-routed so links land where they point. Rooms
 * fetch lazily and cache; write actions invalidate so numbers move.
 */
import { useEffect, useState } from 'react'
import { api } from './api'
import { Hero } from './components/Glance'
import { TabBar } from './components/TabBar'
import { ActivityRoom } from './rooms/Activity'
import { AutopilotRoom } from './rooms/Autopilot'
import { GoalsRoom } from './rooms/Goals'
import { InvestmentsRoom } from './rooms/Investments'
import { MoneyMapRoom } from './rooms/MoneyMap'
import { SpendingRoom } from './rooms/Spending'
import { LoadError } from './components/ui'
import { useFetch } from './useFetch'

const ROOM_IDS = ['spending', 'activity', 'map', 'investments', 'goals', 'autopilot'] as const
type RoomId = (typeof ROOM_IDS)[number]

function hashRoom(): RoomId {
  const h = window.location.hash.replace('#', '')
  return (ROOM_IDS as readonly string[]).includes(h) ? (h as RoomId) : 'spending'
}

export default function App() {
  const [room, setRoom] = useState<RoomId>(hashRoom)
  const glance = useFetch('glance', api.glance)

  useEffect(() => {
    const onHash = () => setRoom(hashRoom())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const pick = (id: string) => {
    window.location.hash = id
    setRoom(id as RoomId)
  }

  const uncat = glance.data ? undefined : undefined
  void uncat

  return (
    <>
      <Hero data={glance.data} />
      <TabBar
        rooms={[
          { id: 'spending', label: 'Spending' },
          { id: 'activity', label: 'Activity' },
          { id: 'map', label: 'Money map' },
          { id: 'investments', label: 'Investments' },
          { id: 'goals', label: 'Goals' },
          { id: 'autopilot', label: 'Autopilot' },
        ]}
        active={room}
        onPick={pick}
      />
      <main className="wrap">
        {glance.error && <LoadError error={glance.error} retry={glance.reload} />}
        <div className="room" role="tabpanel" id={`room-${room}`}>
          {room === 'spending' && <SpendingRoom />}
          {room === 'activity' && <ActivityRoom />}
          {room === 'map' && <MoneyMapRoom />}
          {room === 'investments' && <InvestmentsRoom />}
          {room === 'goals' && <GoalsRoom />}
          {room === 'autopilot' && <AutopilotRoom onGoActivity={() => pick('activity')} />}
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
    </>
  )
}
