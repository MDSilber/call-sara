/** The room tabs — hash-routed like Sara Home so links deep-link.
 * A badge on Activity shows the review queue when it needs a human. */
export interface RoomDef {
  id: string
  label: string
  badge?: number
}

export function TabBar(props: {
  rooms: RoomDef[]
  active: string
  onPick: (id: string) => void
}) {
  return (
    <nav className="wrap">
      <div className="tabbar" role="tablist" aria-label="rooms">
        {props.rooms.map((r) => (
          <button
            key={r.id}
            role="tab"
            className="tab"
            aria-selected={r.id === props.active}
            aria-controls={`room-${r.id}`}
            onClick={() => props.onPick(r.id)}
          >
            {r.label}
            {r.badge ? <span className="badge num">{r.badge}</span> : null}
          </button>
        ))}
      </div>
    </nav>
  )
}
