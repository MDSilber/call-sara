/** The Connections room: every Plaid item's health with its three doors
 * (sync / repair / disable), the lifetime-slots line, and the drop zone —
 * drag a statement in, read the filing plan + dry-run report, confirm, and
 * watch the gated import stream by. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import { useToast } from '../components/toastContext'
import { Card, CardSkeleton, LoadError } from '../components/ui'
import type { ConnectionItem, UploadPlan } from '../types'
import { invalidate, invalidateExcept, useFetch } from '../useFetch'

const MAX_UPLOAD_BYTES = 15 * 1024 * 1024
const ACCEPT = '.ofx,.qfx,.csv,.pdf'
// Plaid Link rides Plaid's CDN script — the ONE external script this app
// ever loads, injected only when a repair starts. Air-gapped installs
// simply see the load fail and keep the terminal recipe.
const PLAID_LINK_SRC = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js'

interface PlaidLinkHandler {
  open: () => void
}

declare global {
  interface Window {
    Plaid?: {
      create: (config: {
        token: string
        onSuccess: () => void
        onExit: (err: unknown) => void
      }) => PlaidLinkHandler
    }
  }
}

export function ConnectionsRoom() {
  const { data, error, loading, reload } = useFetch('connections', api.connections)
  if (loading) return <div className="grid g-half"><CardSkeleton lines={6} /><CardSkeleton lines={6} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null
  return (
    <>
      <div className="grid g-solo" style={{ marginTop: 'var(--sp-4)' }}>
        <DropZone />
      </div>
      <Card k="Linked institutions"
        sub="Each card is one Plaid connection: its freshness, the accounts it feeds, and its three doors. Slots are lifetime — repair is free, disabling keeps the slot."
        className="conncard" window={data.fixture ? 'demo fixture feed' : undefined}>
        {data.configured ? (
          <>
            <div className="conngrid">
              {data.items.map((item) => (
                <Connection key={item.alias} item={item} onChanged={reload} />
              ))}
            </div>
            <p className="slotline num">{data.slots.line}</p>
          </>
        ) : (
          <div className="emptyhero">
            <p className="eh1">No bank connections yet.</p>
            <p className="eh2">
              Link one from a terminal — <code>python -m sara.link ally</code> walks
              the whole flow and prints the config to paste. Manual statement drops
              (above) work without any of it.
            </p>
            {!data.keys_present && (
              <p className="eh2" style={{ marginTop: 8 }}>
                First run: put your Plaid keys in <code>.secrets/plaid.env</code> —
                the link tool prints the exact recipe.
              </p>
            )}
          </div>
        )}
      </Card>
    </>
  )
}

const STATUS_LINE: Record<ConnectionItem['status'], string> = {
  fresh: 'synced',
  stale: 'quiet for',
  dead: 'silent for',
  never: 'never synced',
  'no-token': 'token missing',
}

function Connection(props: { item: ConnectionItem; onChanged: () => void }) {
  const { item } = props
  const toast = useToast()
  const [stream, setStream] = useState<string | null>(null)
  const [busy, setBusy] = useState<'' | 'sync' | 'repair' | 'disable'>('')
  const [confirmDisable, setConfirmDisable] = useState(false)

  const sync = () => {
    if (busy) return
    setBusy('sync')
    setStream('')
    api.plaidSync(item.alias, (chunk) => setStream((s) => (s ?? '') + chunk))
      .then(() => {
        setBusy('')
        // the ingest regenerated everything server-side; refresh the other
        // rooms and swap this page's data in place (the stream stays up)
        invalidateExcept('connections', 'owners')
        props.onChanged()
      })
      .catch((e: unknown) => {
        setBusy('')
        setStream((s) => `${s ?? ''}\n✗ ${e instanceof Error ? e.message : String(e)}`)
      })
  }

  const repair = () => {
    if (busy) return
    setBusy('repair')
    api.linkUpdate(item.alias)
      .then((r) => loadPlaidLink().then((plaid) => {
        const handler = plaid.create({
          token: r.link_token,
          onSuccess: () => {
            setBusy('')
            toast.show(`${item.alias} repaired — no slot spent`, {
              detail: 'run a sync to pull what the bank held back',
            })
            props.onChanged()
          },
          onExit: () => setBusy(''),
        })
        handler.open()
      }))
      .catch((e: unknown) => {
        setBusy('')
        toast.show('Repair could not start', {
          kind: 'err',
          detail: e instanceof ApiError ? e.message
            : 'Plaid Link failed to load — repair from a terminal: '
              + `python -m sara.link --repair ${item.alias}`,
        })
      })
  }

  const disable = () => {
    if (busy) return
    if (!confirmDisable) {
      setConfirmDisable(true)
      window.setTimeout(() => setConfirmDisable(false), 5000)
      return
    }
    setBusy('disable')
    api.plaidDisable(item.alias)
      .then((r) => {
        setBusy('')
        toast.show(`${r.alias} disabled`, { detail: r.note })
        invalidate('connections')
        props.onChanged()
      })
      .catch((e: unknown) => {
        setBusy('')
        toast.show('Disable refused', {
          kind: 'err',
          detail: e instanceof Error ? e.message : String(e),
        })
      })
  }

  const when = item.status === 'fresh' && item.last_synced_lbl
    ? `${STATUS_LINE.fresh} ${item.last_synced_lbl}`
    : item.status === 'stale' || item.status === 'dead'
      ? `${STATUS_LINE[item.status]} ${item.silent_days} day${item.silent_days === 1 ? '' : 's'}`
      : STATUS_LINE[item.status]

  return (
    <div className="conn">
      <div className="connhead">
        <span className="connname">{item.alias}</span>
        <span className={`connstat ${item.status}`}>{item.status}</span>
      </div>
      <p className="connsub">
        {when} · {item.products.join(' + ')}
      </p>
      <ul className="connaccts">
        {item.accounts.map((a) => (
          <li key={a.ledger_account}>
            <span>{a.ledger_account.split(':').slice(2).join(' · ')}</span>
            {a.tail && <span className="tail">…{a.tail}</span>}
          </li>
        ))}
        {item.accounts.length === 0 && <li>no accounts routed yet</li>}
      </ul>
      <div className="connact">
        <button className="btn primary" onClick={sync} disabled={busy !== ''}>
          {busy === 'sync' ? 'Syncing…' : 'Sync now'}
        </button>
        <button className="btn" onClick={repair} disabled={busy !== ''}>
          {busy === 'repair' ? 'Opening…' : 'Repair'}
        </button>
        <button className="btn danger" onClick={disable} disabled={busy !== ''}>
          {busy === 'disable' ? 'Disabling…' : confirmDisable ? 'Really disable?' : 'Disable'}
        </button>
      </div>
      {confirmDisable && (
        <p className="connsub" role="status">
          Disabling comments the config out — the token and the lifetime slot
          stay safe, and re-enabling is an uncomment away.
        </p>
      )}
      {stream !== null && <StreamBox text={stream} />}
    </div>
  )
}

/** A <pre> that follows its own tail while a stream writes into it. */
function StreamBox({ text }: { text: string }) {
  const ref = useRef<HTMLPreElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [text])
  return <pre className="streambox" ref={ref}>{text || 'starting…'}</pre>
}

let plaidLoader: Promise<NonNullable<Window['Plaid']>> | null = null

function loadPlaidLink(): Promise<NonNullable<Window['Plaid']>> {
  if (window.Plaid) return Promise.resolve(window.Plaid)
  plaidLoader ??= new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = PLAID_LINK_SRC
    s.onload = () => (window.Plaid ? resolve(window.Plaid) : reject(new Error('Plaid Link unavailable')))
    s.onerror = () => {
      plaidLoader = null
      reject(new Error('Plaid Link script failed to load'))
    }
    document.head.appendChild(s)
  })
  return plaidLoader
}

// ------------------------------------------------------------- drop zone
type DropPhase =
  | { step: 'idle' }
  | { step: 'planning'; name: string }
  | { step: 'planned'; plan: UploadPlan }
  | { step: 'applying'; plan: UploadPlan; stream: string }
  | { step: 'done'; plan: UploadPlan; stream: string }

function DropZone() {
  const toast = useToast()
  const [phase, setPhase] = useState<DropPhase>({ step: 'idle' })
  const [over, setOver] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const take = useCallback((files: FileList | null) => {
    const file = files?.[0]
    if (!file) return
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.show('Too big', { kind: 'err', detail: 'statements cap at 15MB' })
      return
    }
    setPhase({ step: 'planning', name: file.name })
    api.upload(file)
      .then((plan) => setPhase({ step: 'planned', plan }))
      .catch((e: unknown) => {
        setPhase({ step: 'idle' })
        toast.show('Sara can’t take that file', {
          kind: 'err',
          detail: e instanceof Error ? e.message : String(e),
        })
      })
  }, [toast])

  const confirm = (plan: UploadPlan) => {
    setPhase({ step: 'applying', plan, stream: '' })
    api.uploadConfirm(plan.upload_id, (chunk) =>
      setPhase((p) => p.step === 'applying'
        ? { ...p, stream: p.stream + chunk }
        : p))
      .then(() => {
        setPhase((p) => p.step === 'applying' ? { step: 'done', plan: p.plan, stream: p.stream } : p)
        invalidateExcept('connections', 'owners')
      })
      .catch((e: unknown) => {
        toast.show('Import failed', {
          kind: 'err',
          detail: e instanceof Error ? e.message : String(e),
        })
        setPhase({ step: 'idle' })
      })
  }

  return (
    <Card k="Drop a statement"
      sub="OFX, QFX, CSV, or PDF — Sara identifies it, shows the filing plan and the import’s dry-run, and only writes after you confirm.">
      {phase.step === 'idle' && (
        <div
          className={`dropzone${over ? ' over' : ''}`}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            setOver(true)
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setOver(false)
            take(e.dataTransfer.files)
          }}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') fileRef.current?.click()
          }}
          aria-label="drop a statement file"
        >
          <p className="dz1">Drop a statement here</p>
          <p className="dz2">or click to pick a file · .ofx · .qfx · .csv · .pdf · 15MB max</p>
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            hidden
            onChange={(e) => {
              take(e.target.files)
              e.target.value = ''
            }}
          />
        </div>
      )}
      {phase.step === 'planning' && (
        <div className="emptyhero">
          <p className="eh1">Reading {phase.name}…</p>
          <p className="eh2">identifying the statement and dry-running the import</p>
        </div>
      )}
      {(phase.step === 'planned' || phase.step === 'applying' || phase.step === 'done') && (
        <div className="planbox">
          <p className="plabel">{phase.plan.label}</p>
          <p className="pnote">{phase.plan.recognized
            ? 'Reviewed below is the import’s dry-run — nothing is written until you confirm.'
            : phase.plan.unknown_note}</p>
          <p className="pfile">→ {phase.plan.files_to}</p>
          {phase.plan.report && phase.step === 'planned' && (
            <pre className="streambox">{phase.plan.report}</pre>
          )}
          {(phase.step === 'applying' || phase.step === 'done') && (
            <StreamBox text={phase.stream} />
          )}
          <div className="planact">
            {phase.step === 'planned' && (
              <>
                <button className="btn primary" onClick={() => confirm(phase.plan)}>
                  {phase.plan.recognized ? 'File it and import' : 'File it for Sara'}
                </button>
                <button className="btn quiet" onClick={() => setPhase({ step: 'idle' })}>
                  Cancel
                </button>
              </>
            )}
            {phase.step === 'applying' && <span className="refreshing">working…</span>}
            {phase.step === 'done' && (
              <button className="btn" onClick={() => setPhase({ step: 'idle' })}>
                Drop another
              </button>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}
