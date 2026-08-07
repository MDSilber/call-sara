/** The Investments room v2: positions at the latest prices, per-lot
 * holdings with LT/ST badges and unrealized G/L, the dividends timeline,
 * contribution pace against the IRS limits (year named), and the
 * allocation mix vs the declared targets. Owner-lens aware: positions,
 * lots, dividends, and pace re-slice; the thesis stays household-level. */
import { useCallback, useState } from 'react'
import { api } from '../api'
import { EChart } from '../charts/EChart'
import type { EChartsCoreOption } from '../charts/echarts'
import { FONT, baseOption, tipHtml } from '../charts/options'
import { Card, CardSkeleton, Empty, LoadError, Track } from '../components/ui'
import { useOwner, ownerLabel } from '../ownerLens'
import { cssv } from '../theme'
import type { DividendsTimeline, Investments } from '../types'
import { useFetch } from '../useFetch'

const DONUT_VARS = ['--map-1', '--map-2', '--map-3', '--map-4', '--map-5', '--map-6']

export function InvestmentsRoom() {
  const owner = useOwner()
  const { data, error, loading, reload } = useFetch(
    `investments:${owner}`, () => api.investments(owner))
  if (loading) return <div className="grid g-nwt"><CardSkeleton lines={7} /><CardSkeleton lines={5} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null
  return <InvestBody data={data} owner={owner} />
}

interface DonutParams {
  name: string
  dataIndex: number
}

const LOTS_PREVIEW = 24

function InvestBody({ data, owner }: { data: Investments; owner: string }) {
  const [allLots, setAllLots] = useState(false)
  const alloc = data.allocation
  const lensOn = owner !== 'all'
  const lots = allLots ? data.lots : data.lots.slice(0, LOTS_PREVIEW)

  const buildDonut = useCallback((width: number): EChartsCoreOption | null => {
    if (!alloc || alloc.rows.length === 0) return null
    void width
    const opt = baseOption()
    const tooltip = opt.tooltip as Record<string, unknown>
    tooltip.trigger = 'item'
    tooltip.formatter = (p: DonutParams) => {
      const row = alloc.rows[p.dataIndex]
      return row
        ? tipHtml({ t: row.label, rows: [[row.amt, `${row.pct} of invested · target ${row.target}`]] })
        : ''
    }
    opt.series = [{
      type: 'pie',
      radius: ['58%', '86%'],
      center: ['50%', '50%'],
      padAngle: 1.5,
      itemStyle: { borderRadius: 6, borderColor: cssv('--surface'), borderWidth: 2 },
      label: {
        show: true, position: 'inside', fontFamily: FONT, fontSize: 11.5,
        fontWeight: 600, color: '#fff',
        formatter: (p: DonutParams) => alloc.rows[p.dataIndex]?.pct ?? '',
      },
      emphasis: { scale: false },
      data: alloc.rows.map((r, i) => ({
        name: r.label,
        value: r.value,
        itemStyle: { color: cssv(DONUT_VARS[i % DONUT_VARS.length] ?? '--map-6') },
      })),
    }]
    return opt as EChartsCoreOption
  }, [alloc])

  const pace = data.contribution_pace
  return (
    <>
      <div className="grid g-nwt">
        <div className="sidecol">
          <Card
            k={lensOn ? `Positions · ${ownerLabel(owner)}` : 'Positions'}
            sub="Every holding, valued at the latest price on file — the same numbers the reports carry."
            window={data.window}
          >
            {data.positions.length === 0 ? (
              <Empty><b>No holdings {lensOn ? `under ${ownerLabel(owner)}` : 'on file'}.</b> Import a brokerage statement and the table fills in.</Empty>
            ) : (
              <table className="postable">
                <thead>
                  <tr>
                    <th>Holding</th><th className="r">Units</th><th className="r">Price</th>
                    <th className="r">Value</th><th className="r">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {data.positions.map((p) => (
                    <tr key={p.symbol}>
                      <td className="sym">{p.symbol}</td>
                      <td className="r num">{p.units}</td>
                      <td className="r num">
                        {p.price ?? '—'}
                        {p.price_date && <span style={{ color: 'var(--muted)' }}> · {p.price_date}</span>}
                      </td>
                      <td className="r num"><b>{p.value ?? 'unpriced'}</b></td>
                      <td className="r num">{p.share}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="goalfoot">Invested total <b className="num">{data.invested_total}</b> at the latest prices on file{lensOn ? ' (household)' : ''}.</p>
            {data.paper_note && <p className="goalfoot">{data.paper_note}</p>}
          </Card>
          <Card
            k={lensOn ? `Lots · ${ownerLabel(owner)}` : 'Lots'}
            sub="Each purchase lot as the ledger booked it. LT lots (held a year+) sell at the kinder tax rate."
            window={data.window}
          >
            {data.lots.length === 0 ? (
              <Empty><b>No cost-basis lots yet.</b> Lots appear when buys are booked with their cost — imports do this on their own.</Empty>
            ) : (
              <table className="postable">
                <thead>
                  <tr>
                    <th>Lot</th><th className="r">Units</th><th>Acquired</th>
                    <th className="r">Basis</th><th className="r">Value</th>
                    <th className="r">Unrealized</th>
                  </tr>
                </thead>
                <tbody>
                  {lots.map((lot) => (
                    <tr key={`${lot.account}:${lot.symbol}:${lot.acquired ?? ''}:${lot.basis}`}>
                      <td>
                        <span className="sym">{lot.symbol}</span>
                        <div className="lotacct">{lot.account.split(':').slice(2).join(' · ')}</div>
                      </td>
                      <td className="r num">{lot.units}</td>
                      <td>
                        <span className={`termchip ${lot.term.toLowerCase()}`}>{lot.term}</span>{' '}
                        <span className="num" style={{ color: 'var(--ink-2)', fontSize: 'var(--fs-1)' }}>{lot.acquired_lbl}</span>
                      </td>
                      <td className="r num">{lot.basis}</td>
                      <td className="r num"><b>{lot.value ?? 'unpriced'}</b></td>
                      <td className={`r num ${lot.gain_cls}`}>
                        {lot.gain ?? '—'}
                        {lot.gain_pct && <span style={{ color: 'var(--muted)' }}> · {lot.gain_pct}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {data.lots.length > LOTS_PREVIEW && (
              <button className="btn loadmore" onClick={() => setAllLots((v) => !v)}>
                {allLots
                  ? 'Show fewer'
                  : `Show all ${data.lots.length.toLocaleString()} lots`}
              </button>
            )}
          </Card>
        </div>
        <div className="sidecol">
          <Card k="The mix" sub={alloc ? undefined : 'Declared targets light this up.'}
            window={alloc ? `scored over ${alloc.invested}${lensOn ? ' · household' : ''}` : undefined}>
            {alloc ? (
              <>
                <EChart className="chart chart-donut" build={buildDonut} ariaLabel="allocation by class" />
                {alloc.rows.map((r) => (
                  <div key={r.label} className="setrow">
                    <span className="skey">{r.label}</span>
                    <span className="sval num">{r.amt} · {r.pct} vs {r.target}</span>
                    {r.out && <span className="chip bad">off target</span>}
                  </div>
                ))}
                {alloc.cash_above_reserve && (
                  <p className="goalfoot">Cash above the reserve: <b className="num">{alloc.cash_above_reserve}</b> — outside the scored mix.</p>
                )}
              </>
            ) : (
              <Empty>
                <b>No target mix declared yet.</b> Tell Sara the household&rsquo;s target
                allocation and this card starts scoring drift against it.
              </Empty>
            )}
          </Card>
          <DividendsCard data={data.dividends_timeline} lens={lensOn ? ownerLabel(owner) : null} />
          <Card k="Contribution pace"
            sub={pace.note}
            window={`${pace.year} YTD`}>
            {pace.rows.length === 0 ? (
              <Empty><b>No retirement inflows booked this year.</b> Contributions show up here as 401(k)/IRA accounts see money.</Empty>
            ) : (
              <>
                {pace.rows.map((r) => (
                  <div className="meter" key={r.key}>
                    <div className="mhead">
                      <span><b className="num">{r.contributed}</b> · {r.label}</span>
                      {r.limit && (
                        <span className="mcap">limit {r.limit} ({r.limit_year})</span>
                      )}
                    </div>
                    {typeof r.pct === 'number' && (
                      <Track fill={String(Math.min(100, r.pct))} cls={r.pct >= 100 ? 'over' : ''} />
                    )}
                    {r.room && r.limit && (
                      <div className="barnotes">
                        <span>{r.pct !== undefined && r.pct >= 100 ? 'at (or past) this year’s limit' : `${r.room} of room left`}</span>
                        <span className="num">{r.pct?.toFixed(0)}%</span>
                      </div>
                    )}
                  </div>
                ))}
                {pace.source && (
                  <p className="goalfoot">Limits read from <code>{pace.source}</code>, year named on each row. Employer match rides outside the elective line, so a maxed bar is a win, not an alarm.</p>
                )}
              </>
            )}
          </Card>
          <Card k="The contribution machine" sub="Standing auto-invests, checked against the ledger.">
            {data.contributions.lanes.length === 0 ? (
              <Empty><b>No auto-invest lanes declared.</b> Tell Sara about a standing buy and it gets watched here.</Empty>
            ) : (
              <ul className="lanes">
                {data.contributions.lanes.map((ln) => (
                  <li key={ln.name}>
                    <span className={`lanedot ${ln.status === 'ok' ? 'ok' : ln.status === 'pending' ? 'watch' : 'bad'}`} />
                    <div>
                      <div className="lname">{ln.name}</div>
                      <div className="ldet">
                        {ln.status === 'ok' && ln.last
                          ? `ran ${ln.last}${ln.last_amount ? ` · ${ln.last_amount}` : ''} · ${ln.cadence}`
                          : `${ln.status} · ${ln.cadence}`}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}

interface BarParams {
  dataIndex: number
}

function DividendsCard({ data, lens }: { data: DividendsTimeline; lens: string | null }) {
  const build = useCallback((width: number): EChartsCoreOption | null => {
    if (data.months.length < 2) return null
    void width
    const opt = baseOption()
    const tooltip = opt.tooltip as Record<string, unknown>
    tooltip.trigger = 'item'
    tooltip.formatter = (p: BarParams) => {
      const m = data.months[p.dataIndex]
      return m ? tipHtml({
        t: m.label,
        rows: [[m.amt, `${m.n} payment${m.n === 1 ? '' : 's'}`]],
      }) : ''
    }
    opt.grid = { left: 8, right: 8, top: 8, bottom: 22 }
    opt.xAxis = {
      type: 'category',
      data: data.months.map((m) => m.label),
      axisLine: { lineStyle: { color: cssv('--axis') } },
      axisTick: { show: false },
      axisLabel: {
        color: cssv('--muted'), fontSize: 10.5, fontFamily: FONT,
        interval: Math.max(0, Math.ceil(data.months.length / 8) - 1),
      },
    }
    opt.yAxis = { type: 'value', show: false }
    opt.series = [{
      type: 'bar',
      data: data.months.map((m) => m.value),
      itemStyle: { color: cssv('--accent'), borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 26,
    }]
    return opt as EChartsCoreOption
  }, [data])

  return (
    <Card k={lens ? `Dividends · ${lens}` : 'Dividends'} window={`${data.ytd_count} payment${data.ytd_count === 1 ? '' : 's'} YTD`}>
      <div className="heromini num">{data.ytd}</div>
      <div className="herolab">dividend income booked this year</div>
      {data.months.length >= 2 ? (
        <EChart className="chart chart-div" build={build} ariaLabel="dividends by month" />
      ) : (
        <p className="empty" style={{ paddingBottom: 0 }}>
          The month-by-month timeline draws once two months carry dividends.
        </p>
      )}
    </Card>
  )
}
