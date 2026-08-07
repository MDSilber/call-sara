/** The in-room owner filter: a quiet segmented control (All · each person
 * · Joint) for the rooms where "whose?" is a real question. Local state
 * only — no store, no persistence; every visit starts at All. Hidden
 * until the ledger declares owners with material balance-sheet slices
 * (the server's /api/owners applies the floors). */
import { api } from '../api'
import { useFetch } from '../useFetch'

export function OwnerFilter(props: { owner: string; onPick: (owner: string) => void }) {
  const { data } = useFetch('owners', api.owners)
  if (!data || data.owners.length === 0) return null
  return (
    <div className="roomlens" role="group" aria-label="filter by owner">
      <button className="pchip" aria-pressed={props.owner === 'all'}
        onClick={() => props.onPick('all')}>
        All
      </button>
      {data.owners.map((o) => (
        <button key={o.owner} className="pchip" aria-pressed={props.owner === o.owner}
          onClick={() => props.onPick(o.owner)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}
