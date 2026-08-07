/** The Autopilot room: the machine's lanes, everything that needs a
 * human (with dismiss-until buttons), money that must move (own-account
 * plumbing folded), and the review queue. */
import { useState } from 'react'
import { api } from '../api'
import { useToast } from '../components/toastContext'
import { civil, friendlyDate } from '../civil'
import { CodeText, Card, CardSkeleton, Empty, LoadError } from '../components/ui'
import type { Autopilot, Move } from '../types'
import { invalidate, useFetch } from '../useFetch'

const DISMISS_DAYS = 30

/** '2026-09-06' -> 'Sep 6' — toast dates read like a person wrote them. */
function monD(iso: string): string {
  return friendlyDate(iso).replace(/, \d{4}$/, '')
}

export function AutopilotRoom(props: { onGoActivity: () => void }) {
  const { data, error, loading, reload } = useFetch('autopilot', api.autopilot)
  if (loading) return <div className="grid g-needs"><CardSkeleton lines={6} /><CardSkeleton lines={5} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null
  return <AutoBody data={data} onGoActivity={props.onGoActivity} />
}

function AutoBody({ data, onGoActivity }: { data: Autopilot; onGoActivity: () => void }) {
  const toast = useToast()
  const [busyId, setBusyId] = useState<string | null>(null)

  const dismiss = (id: string, title: string) => {
    setBusyId(id)
    const until = new Date(Date.now() + DISMISS_DAYS * 86400_000)
      .toISOString().slice(0, 10)
    api.dismiss(id, until, title)
      .then(() => {
        toast.show(`Quiet until ${monD(until)}`, {
          detail: title.slice(0, 60),
          undo: () => {
            api.dismiss(id, null)
              .then(() => invalidate('autopilot', 'glance'))
              .catch(() => toast.show('Undo failed', { kind: 'err' }))
          },
        })
        invalidate('autopilot', 'glance')
      })
      .catch((e: unknown) => {
        toast.show('Dismiss refused', { kind: 'err', detail: e instanceof Error ? e.message : String(e) })
        setBusyId(null)
      })
  }

  return (
    <>
      <div className="grid g-needs">
        <Card k="Needs you" sub="Anything that wants a decision or a quick hand — dismiss what can wait." window={data.checks_from ? `checks from ${data.checks_from}` : undefined}>
          {!data.findings_ran ? (
            <Empty><b>The checks have never run.</b> Ask Sara to run them and this list starts watching for you.</Empty>
          ) : data.queue.length === 0 ? (
            <div className="allclear">
              <b>All clear.</b>
              <span className="s">Nothing needs a decision — {data.counts || 'the checks ran clean'}.</span>
            </div>
          ) : (
            <ul className="needs">
              {data.queue.map((q) => (
                <li key={q.id}>
                  <span className={`sevdot ${q.severity}`} />
                  <div style={{ minWidth: 0 }}>
                    <div className="verb"><CodeText text={civil(q.fix || q.title)} /></div>
                    <div className="why"><CodeText text={civil(q.title)} /></div>
                  </div>
                  <span className="lmeta">
                    {q.severity}
                    <button
                      className="dismissbtn"
                      disabled={busyId === q.id}
                      onClick={() => dismiss(q.id, q.title)}
                      title={`quiet for ${DISMISS_DAYS} days`}
                    >
                      dismiss
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {data.errors.length > 0 && (
            <p className="nudge">A check needs fixing before it can watch for you: {data.errors[0]}</p>
          )}
          {data.dismissed.filter((d) => d.active).length > 0 && (
            <p className="morelink">
              {data.dismissed.filter((d) => d.active).length} dismissed — they come back on their date, or the moment the finding changes.
            </p>
          )}
        </Card>
        <div className="sidecol">
          <Card k="The machine" sub={data.machine.sub} window={data.machine.window}>
            {data.machine.rows.length === 0 ? (
              <Empty><b>Nothing wired yet.</b> Tell Sara your standing orders (paycheck, auto-invest, floors) and the machine watches them.</Empty>
            ) : (
              <>
                <ul className="lanes">
                  {data.machine.rows.map((ln) => (
                    <li key={ln.name}>
                      <span className={`lanedot ${ln.dot}`} />
                      <div style={{ minWidth: 0 }}>
                        <div className="lname">{ln.name}</div>
                        <div className={`ldet${ln.dot === 'bad' ? ' bad' : ''}`}>{ln.detail}</div>
                      </div>
                      <span className="lanetag">{ln.tag}</span>
                    </li>
                  ))}
                </ul>
                {data.machine.summary && <p className="lanesum">{data.machine.summary}</p>}
              </>
            )}
          </Card>
          <Card k="Review queue">
            {data.review.count === 0 ? (
              <div className="allclear"><b>Zero uncategorized.</b><span className="s">Every dollar has a name.</span></div>
            ) : (
              <>
                <div className="heromini num">{data.review.count} <span className="of">to categorize · {data.review.amount}</span></div>
                <p className="herolab">{data.review.note}</p>
                <div style={{ marginTop: 10 }}>
                  <button className="btn primary" onClick={onGoActivity}>Open the Activity room</button>
                </div>
              </>
            )}
          </Card>
        </div>
      </div>
      <div className="grid g-solo">
        <MovesCard moves={data.moves} plumbing={data.plumbing} />
      </div>
    </>
  )
}

function MovesCard({ moves, plumbing }: { moves: Move[]; plumbing: Move[] }) {
  const [showPlumbing, setShowPlumbing] = useState(false)
  return (
    <Card k="Money that must move" sub="Dated obligations from the household's own facts — nothing invented." window="next 60 days">
      {moves.length === 0 && plumbing.length === 0 ? (
        <Empty><b>Nothing on the calendar.</b> Dated bullets with dollar figures in facts/ appear here.</Empty>
      ) : (
        <>
          <ul className="moves">
            {moves.map((mv) => (
              <li key={`${mv.date}:${mv.text.slice(0, 30)}`}>
                <span className={`mvdate num${mv.near ? ' near' : ''}`}>{mv.day_lbl}</span>
                <span className="mvamt num">{mv.amt}</span>
                <span className="mvtext">{mv.text} · {mv.when}</span>
              </li>
            ))}
          </ul>
          {plumbing.length > 0 && (
            <>
              <button className="morecats" onClick={() => setShowPlumbing((s) => !s)}>
                {showPlumbing ? 'Hide' : 'Show'} {plumbing.length} own-account move{plumbing.length === 1 ? '' : 's'} (plumbing)
              </button>
              {showPlumbing && (
                <ul className="moves">
                  {plumbing.map((mv) => (
                    <li key={`${mv.date}:${mv.text.slice(0, 30)}`}>
                      <span className={`mvdate num${mv.near ? ' near' : ''}`}>{mv.day_lbl}</span>
                      <span className="mvamt num">{mv.amt}</span>
                      <span className="mvtext">{mv.text} · {mv.when}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </Card>
  )
}
