/** The Goals room: the 529 story with its what-if slider (precomputed
 * grid — the slider only looks strings up), the college target, and the
 * one-time college-target question whose "not now" sticks. */
import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { api } from '../api'
import { useToast } from '../components/toastContext'
import { Card, CardSkeleton, Empty, LoadError, Track } from '../components/ui'
import type { Goals } from '../types'
import { invalidate, useFetch } from '../useFetch'

const ASK_QUIET_DAYS = 365

export function GoalsRoom() {
  const { data, error, loading, reload } = useFetch('goals', api.goals)
  if (loading) return <div className="grid g-half"><CardSkeleton lines={6} /><CardSkeleton lines={5} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null
  return <GoalsBody data={data} />
}

function GoalsBody({ data }: { data: Goals }) {
  const edu = data.education
  const grid = edu.grid
  const [step, setStep] = useState(grid?.def ?? 0)
  const targetInput = useRef<HTMLInputElement>(null)
  const asking = Boolean(data.ask && !data.ask.dismissed)
  return (
    <div className="grid g-half">
      <Card k={edu.title} sub={edu.sub} window={data.window}>
        {edu.empty ? (
          <Empty>{edu.empty}</Empty>
        ) : (
          <>
            <div className="heromini num">
              {edu.value} {edu.of && <span className="of">{edu.of}</span>}
            </div>
            {edu.val_lbl && <div className="herolab">{edu.val_lbl}</div>}
            {edu.fill != null && (
              <>
                <div className="barwrap">
                  <Track fill={edu.fill} cls="edu" trackCls="edu" />
                </div>
                <div className="barnotes">
                  <span>{edu.pct} of the way</span>
                </div>
              </>
            )}
            {edu.perkid && edu.perkid.length > 0 && edu.perkid.map((k) => (
              <div className="envrow" key={k.name}>
                <span className="name">{k.name}</span>
                <Track fill={k.width} cls="edu" trackCls="edu" />
                <span className="amt">{k.amt}</span>
              </div>
            ))}
            {edu.foot && <p className="goalfoot">{edu.foot}</p>}
            {/* the question card asks once — no second nudge on the same screen */}
            {edu.nudge && !asking && <p className="nudge">{edu.nudge}</p>}
            {grid && (
              <div className="eduwhatif">
                <div className="dial">
                  <label htmlFor="edu-slider">What if we contributed…</label>
                  <span className="dval num">{grid.steps[step]}</span>
                  <input
                    id="edu-slider"
                    type="range"
                    min={0}
                    max={grid.steps.length - 1}
                    value={step}
                    onChange={(e) => setStep(Number(e.target.value))}
                    aria-valuetext={grid.steps[step]}
                  />
                </div>
                <div className="edu-out num">{grid.arrive[step]}</div>
                {grid.cover[step] && <div className="edu-out2">{grid.cover[step]}</div>}
                {grid.paceS && (
                  <p className="goalfoot">Current pace: <b className="num">{grid.paceS}</b> (median of recent contributions). Straight-line math in today&rsquo;s dollars — market growth not assumed.</p>
                )}
              </div>
            )}
          </>
        )}
      </Card>
      <div className="sidecol">
        {data.ask && !data.ask.dismissed && (
          <AskCard
            id={data.ask.id}
            onSetTarget={() => targetInput.current?.focus()}
          />
        )}
        <TargetsCard settings={data.settings} inputRef={targetInput} />
      </div>
    </div>
  )
}

/** The one-time question: a 529 exists, no finish line does. "Not now"
 * sticks for a year through the same dismissals door the findings use. */
function AskCard(props: { id: string; onSetTarget: () => void }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const notNow = () => {
    setBusy(true)
    const until = new Date(Date.now() + ASK_QUIET_DAYS * 86400_000)
      .toISOString().slice(0, 10)
    api.dismiss(props.id, until, 'College target question')
      .then(() => {
        toast.show('Okay — parked', { detail: 'set a target any time below' })
        invalidate('goals', 'glance')
      })
      .catch((e: unknown) => {
        setBusy(false)
        toast.show('Could not park it', {
          kind: 'err',
          detail: e instanceof Error ? e.message : String(e),
        })
      })
  }
  return (
    <Card k="One question" className="askcard">
      <p className="askline">
        Want a college finish line? Give the 529 a target and this room
        starts pacing toward a date.
      </p>
      <div className="askact">
        <button className="btn primary" onClick={props.onSetTarget}>
          Pick a number
        </button>
        <button className="btn quiet" onClick={notNow} disabled={busy}>
          Not now
        </button>
      </div>
    </Card>
  )
}

const SETTING_META: Record<string, { label: string; hint: string }> = {
  education_target: {
    label: 'College target',
    hint: 'the 529 finish line, in dollars',
  },
}

function TargetsCard(props: {
  settings: Goals['settings']
  inputRef: RefObject<HTMLInputElement | null>
}) {
  const toast = useToast()
  const rows = props.settings.filter((s) => s.key in SETTING_META)
  return (
    <Card k="Targets" sub="Saved to your vault — every report reads the same number.">
      {rows.map((s) => (
        <TargetRow key={s.key} k={s.key} value={s.value} toastShow={toast.show}
          inputRef={props.inputRef} />
      ))}
    </Card>
  )
}

function TargetRow(props: {
  k: string
  value: number | string | null
  toastShow: ReturnType<typeof useToast>['show']
  inputRef?: RefObject<HTMLInputElement | null>
}) {
  const meta = SETTING_META[props.k]
  const [draft, setDraft] = useState(() =>
    props.value == null ? '' : fmtInput(props.value))
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    setDraft(props.value == null ? '' : fmtInput(props.value))
  }, [props.value])
  const dirty = draft !== (props.value == null ? '' : fmtInput(props.value))
  if (!meta) return null
  const save = () => {
    const num = Number(draft.replace(/[$,\s]/g, ''))
    if (!Number.isFinite(num) || num < 0) {
      props.toastShow('Needs a number', { kind: 'err', detail: `${meta.label} stays as it was` })
      return
    }
    setBusy(true)
    api.setGoal(props.k, num)
      .then((res) => {
        props.toastShow(`${meta.label} saved`, {
          detail: `now ${fmt(num)} (was ${res.previous == null ? 'unset' : fmt(Number(res.previous))})`,
        })
        setDraft(fmtInput(num))
        invalidate()
      })
      .catch((e: unknown) => {
        props.toastShow('Change refused', { kind: 'err', detail: e instanceof Error ? e.message : String(e) })
      })
      .finally(() => setBusy(false))
  }
  return (
    <div className="setrow">
      <span className="skey">{meta.label}</span>
      <input
        ref={props.inputRef}
        type="text"
        inputMode="numeric"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        aria-label={meta.label}
      />
      <button className="btn" disabled={!dirty || busy} onClick={save}>
        {busy ? 'Saving…' : 'Save'}
      </button>
      <span className="shint">{meta.hint}</span>
    </div>
  )
}

/** Display-only echo of what the user just typed (input formatting, not
 * ledger math — every report figure still comes from the server). */
function fmt(n: number): string {
  return `$${Math.round(n).toLocaleString('en-US')}`
}

function fmtInput(v: number | string): string {
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('en-US') : String(v)
}
