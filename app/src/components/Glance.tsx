/** The glance: the ink hero (greeting, Sara's verdict line, ⌘K, theme,
 * stamp), four verdict tiles floating over the band, and the ONE Next
 * line. Verdict words lead; numbers are second — Sara Home's law.
 *
 * The glance is the household view, always. Per-person slicing lives as
 * quiet filters inside the rooms that can answer it (Spending, Activity,
 * Investments) — the hero never re-slices. */
import { useEffect, useState } from 'react'
import { cycleTheme, themeLabel } from '../theme'
import type { Glance as GlanceData } from '../types'
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
            </div>
          </div>
        </div>
      </header>
      <div className="wrap">
        <Tiles data={d} />
        <NextLine data={d} />
      </div>
    </>
  )
}

function Tiles({ data }: { data: GlanceData | null }) {
  if (!data) {
    return (
      <div className="tiles" aria-busy="true">
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
  return (
    <div className="tiles">
      <div className="tile">
        <div className="tk">Spending</div>
        <div className={`tv-big ${t.spend.cls}`}>{t.spend.verdict}</div>
        {t.spend.fig && <div className="tfig num">{t.spend.fig}</div>}
        <div className="tsub">{t.spend.sub}</div>
        {t.spend.streak && <span className="streak">{t.spend.streak}</span>}
      </div>
      <div className="tile">
        <div className="tk">Net worth</div>
        <div className="kv num">{t.networth.value}</div>
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
      </div>
      <div className="tile">
        <div className="tk">Autopilot</div>
        <div className={`tv-big ${t.autopilot.cls}`}>{t.autopilot.verdict}</div>
        <div className="dots" role="img" aria-label={t.autopilot.aria}>
          {t.autopilot.dots.map((dot, i) => (
            <i key={i} className={dot} />
          ))}
        </div>
        <div className="tsub">{t.autopilot.sub}</div>
      </div>
      <div className="tile">
        <div className="tk">{t.education.label}</div>
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
