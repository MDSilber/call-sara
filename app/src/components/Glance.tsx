/** The glance: the ink hero (greeting, Sara's verdict line, the owner lens,
 * ⌘K, theme, stamp), four verdict tiles floating over the band, and the ONE
 * Next line. Verdict words lead; numbers are second — Sara Home's law.
 *
 * Under a person lens the tiles that can re-slice do (net worth shows the
 * owner's liquid slice, Spending shows their pace verdict) and the
 * household-only ones say so with a quiet badge instead of pretending.
 * Sara's line and the Next line stay household — they honestly are. */
import { useEffect, useState } from 'react'
import { api } from '../api'
import { ownerLabel, setOwner, useOwner } from '../ownerLens'
import { cycleTheme, themeLabel } from '../theme'
import type { Glance as GlanceData } from '../types'
import { useFetch } from '../useFetch'
import { CodeText, Skeleton } from './ui'

const IS_MAC = navigator.platform.toUpperCase().includes('MAC')

/** Sara's hero line is a verdict, not directions. Newer snapshots are born
 * clean; older ones may still carry on-page navigation ("Start with the
 * Next line below") — strip those sentences conservatively, and never
 * blank the line. */
const NAV_SPEAK = /next line|below|autopilot/i

function saraVerdict(line: string): string {
  const sentences = line.split(/(?<=[.!?])\s+/)
  const kept = sentences.filter((s) => !NAV_SPEAK.test(s)).join(' ').trim()
  if (kept) return kept
  const clauses = line
    .split(/\s+—\s+/)
    .filter((s) => !NAV_SPEAK.test(s))
    .join(' — ')
    .trim()
  if (!clauses) return line
  return /[.!?]$/.test(clauses) ? clauses : `${clauses}.`
}

/** The hero's tint follows the clock (morning gold, evening violet) —
 * Sara greets by daypart, the room should too. */
function daypart(): string {
  const h = new Date().getHours()
  return h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'
}

export function Hero(props: {
  data: GlanceData | null
  refreshing?: boolean
  onPalette?: () => void
}) {
  const [themeName, setThemeName] = useState(themeLabel())
  const owner = useOwner()
  const d = props.data
  useEffect(() => {
    document.documentElement.dataset.daypart = daypart()
  }, [])
  return (
    <>
      <header className="hero">
        <div className="wrap hero-in">
          <div className="hero-top">
            <div className="hero-lede">
              <div className="hi">{d ? d.greet : 'Sara App'}</div>
              <p className="say">{d ? saraVerdict(d.sara) : '…'}</p>
            </div>
            <div className="hero-side">
              <div className="hero-controls">
                <OwnerLens />
                {props.onPalette && (
                  <button className="herobtn" onClick={props.onPalette}
                    aria-label="open the command palette">
                    Jump <kbd>{IS_MAC ? '⌘K' : 'ctrl K'}</kbd>
                  </button>
                )}
                <button
                  className="themebtn"
                  onClick={() => setThemeName(cycleTheme() === 'auto' ? 'Auto theme' : themeLabel())}
                >
                  {themeName}
                </button>
              </div>
              {d && (
                <div className="stamp">
                  {d.ledger_stamp}
                  {props.refreshing
                    ? ' · refreshing the numbers…'
                    : d.checks_stamp ? ` · ${d.checks_stamp}` : ''}
                </div>
              )}
              {owner !== 'all' && (
                <p className="lenschip" role="status">
                  viewing <b>{ownerLabel(owner)}’s</b> slice — household items badged
                </p>
              )}
            </div>
          </div>
        </div>
      </header>
      <div className="wrap">
        <Tiles data={d} owner={owner} />
        <NextLine data={d} />
      </div>
    </>
  )
}

/** All / per-person / Joint — rendered only once the ledger declares
 * owners; the choice persists and every lens-aware room re-slices. */
function OwnerLens() {
  const owner = useOwner()
  const { data } = useFetch('owners', api.owners)
  if (!data || data.owners.length === 0) return null
  return (
    <div className="lens" role="group" aria-label="owner lens">
      <button aria-pressed={owner === 'all'} onClick={() => setOwner('all')}>
        All
      </button>
      {data.owners.map((o) => (
        <button key={o.owner} aria-pressed={owner === o.owner}
          onClick={() => setOwner(o.owner)}>
          {ownerLabel(o.owner)}
        </button>
      ))}
    </div>
  )
}

/** The quiet corner chip on tiles that stay household-wide under a lens. */
function Hbadge() {
  return <span className="hbadge">household</span>
}

function Tiles({ data, owner }: { data: GlanceData | null; owner: string }) {
  const lens = owner !== 'all'
  // both fetches share caches: 'owners' with the lens pills, the spend key
  // with the Spending room — no duplicate traffic once either has loaded
  const owners = useFetch('owners', api.owners)
  const spend = useFetch(`spend:${owner}`, () => api.spend(owner))
  if (!data) {
    return (
      <div className="tiles" key={owner} aria-busy="true">
        {[0, 1, 2, 3].map((i) => (
          <div className="tile" key={i}>
            <Skeleton h={11} w="55%" />
            <div style={{ height: 8 }} />
            <Skeleton h={24} w="80%" />
            <div style={{ height: 6 }} />
            <Skeleton h={12} w="65%" />
          </div>
        ))}
      </div>
    )
  }
  const t = data.tiles
  const who = ownerLabel(owner)
  const slice = lens ? owners.data?.slices.find((s) => s.owner === owner) : undefined
  const ownerSpend = lens && spend.data?.owner === owner ? spend.data.tile : null
  const spendTile = ownerSpend ?? t.spend
  return (
    // the key replays the entrance ONLY when the lens flips (the skeleton
    // carries the same key, so first data arrival updates in place)
    <div className="tiles" key={owner}>
      <div className="tile">
        <div className="tk">
          {ownerSpend ? `${who}’s spending` : 'Spending'}
          {lens && !ownerSpend && <Hbadge />}
        </div>
        <div className={`tv-big ${spendTile.cls}`}>{spendTile.verdict}</div>
        {spendTile.fig && <div className="tfig num">{spendTile.fig}</div>}
        <div className="tsub">{spendTile.sub}</div>
        {spendTile.streak && <span className="streak">{spendTile.streak}</span>}
      </div>
      <div className="tile">
        <div className="tk">
          {lens && slice ? `${who}’s net worth` : 'Net worth'}
          {lens && !slice && <Hbadge />}
        </div>
        <div className="kv num">{lens && slice ? slice.liquid : t.networth.value}</div>
        {lens && slice ? (
          <div className="tsub">of {t.networth.value} household</div>
        ) : (
          <>
            {t.networth.delta && (
              <span className={`chip mini ${t.networth.delta.cls}`}>
                <b>{t.networth.delta.text.split(' ')[0]}</b>
                {t.networth.delta.text.split(' ').slice(1).join(' ')}
              </span>
            )}
            {t.networth.spark && (
              <svg
                className="spark"
                viewBox="0 0 100 30"
                preserveAspectRatio="none"
                role="img"
                aria-label={`net worth, ${t.networth.spark.win}`}
              >
                <polygon points={t.networth.spark.area} />
                <polyline points={t.networth.spark.points} />
              </svg>
            )}
            <div className="tsub">{t.networth.sub}</div>
          </>
        )}
      </div>
      <div className="tile">
        <div className="tk">Autopilot{lens && <Hbadge />}</div>
        <div className={`tv-big ${t.autopilot.cls}`}>{t.autopilot.verdict}</div>
        <div className="dots" role="img" aria-label={t.autopilot.aria}>
          {t.autopilot.dots.map((dot, i) => (
            <i key={i} className={dot} />
          ))}
        </div>
        <div className="tsub">{t.autopilot.sub}</div>
      </div>
      <div className="tile">
        <div className="tk">{t.education.label}{lens && <Hbadge />}</div>
        {t.education.verdict ? (
          <div className={`tv-big ${t.education.cls}`}>{t.education.verdict}</div>
        ) : (
          <div className="kv num">{t.education.fig}</div>
        )}
        {t.education.verdict && t.education.fig && (
          <div className="tfig num">{t.education.fig}</div>
        )}
        <div className="tsub">{t.education.sub}</div>
      </div>
    </div>
  )
}

function NextLine({ data }: { data: GlanceData | null }) {
  if (!data) {
    return (
      <div className="next" aria-busy="true">
        <Skeleton h={16} w="70%" />
      </div>
    )
  }
  const n = data.next
  return (
    <div className={`next${n.quiet ? ' allquiet' : ''}`}>
      <span className="nk">{n.label}</span>
      <span className="nt">
        <CodeText text={n.text} />
      </span>
      {n.meta && <span className="nm">{n.meta}</span>}
    </div>
  )
}
