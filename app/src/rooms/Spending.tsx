/** The Spending room: is-this-month-unusual pace chart, the clickable
 * category rail with its merchant drill, money in/out, and Sara's wins.
 * Every figure is a server string; charts get geometry only. */
import { useCallback, useMemo, useState } from 'react'
import { api } from '../api'
import { EChart } from '../charts/EChart'
import type { EChartsCoreOption } from '../charts/echarts'
import { areaGrad, baseOption, catAxis, legendBox, nowDot, tipHtml, valAxis } from '../charts/options'
import { cssv } from '../theme'
import type { Spend, SpendCat, Tip } from '../types'
import { Card, CardSkeleton, Empty, LoadError, Track } from '../components/ui'
import { useOwner, ownerLabel } from '../ownerLens'
import { useFetch } from '../useFetch'

type PeriodKey = 'cur' | 'prev' | 'six'

export function SpendingRoom() {
  const owner = useOwner()
  const { data, error, loading, reload } = useFetch(`spend:${owner}`, () => api.spend(owner))
  if (loading) return <div className="grid g-pace"><CardSkeleton lines={7} /><CardSkeleton lines={5} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null
  // key by owner so a lens flip re-enters the room (the entrance motion
  // marks what changed; prefers-reduced-motion already gates it)
  return <SpendingBody key={owner} data={data} />
}

function SpendingBody({ data }: { data: Spend }) {
  const rooms = data.rooms
  const [period, setPeriod] = useState<PeriodKey>(() => {
    if (!rooms) return 'cur'
    const withRows = rooms.periods.find((p) => (rooms.order[p.key] ?? []).length > 0)
    return withRows?.key ?? 'cur'
  })
  const order = useMemo(() => rooms?.order[period] ?? [], [rooms, period])
  const [catIdx, setCatIdx] = useState<number>(order[0] ?? -1)
  const [showAll, setShowAll] = useState(false)
  const active = order.includes(catIdx) ? catIdx : (order[0] ?? -1)
  const cat: SpendCat | null = rooms && active >= 0 ? (rooms.cats[active] ?? null) : null
  const perNow = rooms?.periods.find((p) => p.key === period)

  const buildPace = useCallback((width: number): EChartsCoreOption | null => {
    const P = data.pace_chart
    if (!P) return null
    const opt = baseOption()
    opt.grid = { left: 62, right: 16, top: width < 520 ? 88 : 38, bottom: 28 }
    opt.legend = legendBox()
    opt.xAxis = catAxis(P.days, P.xint, width)
    opt.yAxis = valAxis(P.y)
    ;(opt.tooltip as Record<string, unknown>).formatter = (ps: { dataIndex: number }[]) => {
      const tip = P.tips[ps[0]?.dataIndex ?? 0]
      return tip ? tipHtml(tip) : ''
    }
    const series: Record<string, unknown>[] = []
    if (P.ideal.length) {
      series.push({
        name: 'a typical month, day by day', type: 'line', data: P.ideal,
        symbol: 'none', color: cssv('--ideal'),
        lineStyle: { type: 'dotted', width: 2.5 },
        emphasis: { disabled: true }, z: 1,
      })
    }
    if (P.actual.some((v) => v !== null)) {
      series.push({
        name: 'spent, day by day', type: 'line', data: P.actual,
        symbol: 'none', color: cssv('--accent'),
        lineStyle: { width: 3, cap: 'round' },
        areaStyle: { color: areaGrad('--accent', 0.22) },
        emphasis: { disabled: true }, z: 2,
      })
    }
    if (P.now) series.push(nowDot(P.now.xy, P.now.label, P.now.side))
    opt.series = series
    return opt as EChartsCoreOption
  }, [data.pace_chart])

  const buildTrend = useCallback((width: number): EChartsCoreOption | null => {
    if (!rooms || !cat) return null
    void width
    const opt = baseOption()
    opt.grid = { left: 56, right: 12, top: 14, bottom: 26 }
    opt.xAxis = {
      type: 'category', data: rooms.months,
      axisLine: { lineStyle: { color: cssv('--axis') } },
      axisTick: { show: false },
      axisLabel: { color: cssv('--muted'), fontSize: 11 },
    }
    opt.yAxis = valAxis(cat.y)
    ;(opt.tooltip as Record<string, unknown>).formatter = (ps: { dataIndex: number }[]) => {
      const tip: Tip | undefined = cat.tips[ps[0]?.dataIndex ?? 0]
      return tip ? tipHtml(tip) : ''
    }
    opt.series = [{
      type: 'bar',
      data: cat.series.map((v, i) =>
        i === rooms.partialIdx ? { value: v, itemStyle: { opacity: 0.45 } } : v),
      barMaxWidth: 34,
      itemStyle: { color: cssv('--accent'), borderRadius: [4, 4, 0, 0] },
      emphasis: { disabled: true },
    }]
    return opt as EChartsCoreOption
  }, [rooms, cat])

  const pd = cat?.per[period]
  return (
    <>
      <div className="grid g-pace">
        <Card k={data.pace.title ?? 'Is this month unusual?'} sub={data.pace.sub}
          window={data.pace.window}>
          {data.pace.empty ? (
            <Empty><b>Nothing to pace yet.</b> Import a statement and the line starts drawing.</Empty>
          ) : (
            <>
              {data.pace.hero && (
                <>
                  <div className={`phero num ${data.pace.hero_cls ?? ''}`}>{data.pace.hero}</div>
                  <div className="herolab">{data.pace.herolab}</div>
                </>
              )}
              {data.pace.lag_note && <p className="sub">{data.pace.lag_note}</p>}
              <EChart className="chart chart-pace" build={buildPace}
                ariaLabel="cumulative spending versus the typical path" />
            </>
          )}
        </Card>
        <div className="sidecol">
          <Card k={data.cheshbon.title} window={data.cheshbon.window}>
            <div className="statrow">
              <div className="stat"><div className="l">money in</div><div className="v num">{data.cheshbon.inc}</div></div>
              <div className="stat"><div className="l">money out</div><div className="v num">{data.cheshbon.exp}</div></div>
              <div className="stat"><div className="l">net</div>
                <div className={`v num ${data.cheshbon.net_cls}`}>{data.cheshbon.net}</div></div>
            </div>
            {data.cheshbon.payday_note && <p className="sub" style={{ marginTop: 8 }}>{data.cheshbon.payday_note}</p>}
            {data.cheshbon.closed && (
              <p className="goalfoot">
                {data.cheshbon.closed.month} closed at {data.cheshbon.closed.inc} in ·{' '}
                {data.cheshbon.closed.exp} out · net <b className="num">{data.cheshbon.closed.net}</b>
                {data.cheshbon.wink}
              </p>
            )}
          </Card>
          {data.wins && (
            <Card k="Sara’s finds" badge={data.owner ? 'household' : undefined}
              sub={data.wins.count_lbl} window={data.wins.year}>
              <div className="winhero num">{data.wins.total} <span className="of">found this year</span></div>
              <ul className="wins-list">
                {data.wins.rows.map((w) => (
                  <li key={w.label}><span>{w.label}</span><b>{w.amt}</b></li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>

      {rooms ? (
        <div className="grid g-spend">
          <Card k="Where it went" sub="Click a category for its trend and merchants.">
            <div className="periodbar" role="group" aria-label="period">
              {rooms.periods.map((p) => (
                <button key={p.key} className="pchip" aria-pressed={p.key === period}
                  onClick={() => { setPeriod(p.key); setShowAll(false) }}>
                  {p.label}
                </button>
              ))}
              {perNow && <span className="periodwin num">{perNow.total} · {perNow.win}</span>}
            </div>
            <div className="catrail">
              {(showAll ? order : order.slice(0, rooms.visible)).map((ci) => {
                const c = rooms.cats[ci]
                const per = c?.per[period]
                if (!c || !per) return null
                return (
                  <button key={c.name} className="catrow" aria-pressed={ci === active}
                    onClick={() => setCatIdx(ci)}>
                    <span className="name">{c.name}</span>
                    <Track fill={String(per.w)} />
                    <span className="amt"><b>{per.amt}</b> · {per.pct}</span>
                  </button>
                )
              })}
            </div>
            {order.length > rooms.visible && (
              <button className="morecats" onClick={() => setShowAll((s) => !s)}>
                {showAll ? 'Show fewer'
                  : `+ ${order.length - rooms.visible} smaller categor${order.length - rooms.visible === 1 ? 'y' : 'ies'}`}
              </button>
            )}
          </Card>
          <Card className="drill" k="The drill" window={rooms.trendWin}>
            {cat && pd ? (
              <>
                <h3>{cat.name}</h3>
                <div className="dwin num">{pd.amt} · {perNow?.win}</div>
                <EChart className="chart chart-trend" build={buildTrend}
                  ariaLabel={`${cat.name} by month`} />
                <ul className="merch">
                  {pd.merch.map(([name, amt]) => (
                    <li key={name}><span className="mn">{name}</span><b>{amt}</b></li>
                  ))}
                  {pd.merch.length === 0 && <li><span className="mn">no merchants in this window</span></li>}
                </ul>
                {pd.more > 0 && (
                  <p className="merchmore">+ {pd.more} smaller merchant{pd.more === 1 ? '' : 's'} — the ledger has receipts</p>
                )}
              </>
            ) : (
              <Empty>Pick a category on the left.</Empty>
            )}
          </Card>
        </div>
      ) : (
        <div className="grid g-solo">
          <Card k="Where it went">
            <Empty><b>No spending on file yet.</b> The rail fills in with the first imported statement.</Empty>
          </Card>
        </div>
      )}
      <InsightsStrip />
    </>
  )
}


/** The insights strip: a small-multiple sparkline per top category, drawn
 * from the analytics DB's monthly_flows — the exploratory read path. */
function InsightsStrip() {
  const owner = useOwner()
  const { data, error, loading, reload } = useFetch(
    `insights:${owner}`, () => api.insights(owner))
  if (loading) return <div className="grid g-solo"><CardSkeleton lines={3} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data || data.cats.length === 0) return null
  return (
    <div className="grid g-solo">
      <Card
        k={owner !== 'all' ? `Category trends · ${ownerLabel(owner)}` : 'Category trends'}
        sub="Each spark is one category's monthly spend — the quiet way to spot a bill creeping."
        window={data.window}
      >
        <div className="insights">
          {data.cats.map((c) => (
            <div className="insight" key={c.name}>
              <div className="iname">{c.name}</div>
              <div className="icur num">{c.cur}</div>
              <Sparkline series={c.series} />
              <div className="imeta">
                <span className={`idelta ${c.delta_cls} num`}>{c.delta}</span>
                <span className="num">avg {c.avg}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

/** Plain-SVG sparkline: geometry only, no money math. */
function Sparkline({ series }: { series: number[] }) {
  const max = Math.max(...series, 1)
  const w = 100
  const h = 30
  const step = series.length > 1 ? w / (series.length - 1) : w
  const pts = series
    .map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - (v / max) * (h - 6)).toFixed(1)}`)
    .join(' ')
  const area = `0,${h} ${pts} ${w},${h}`
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
      <line x1="0" y1={h - 0.5} x2={w} y2={h - 0.5} />
      <polygon points={area} />
      <polyline points={pts} />
    </svg>
  )
}
