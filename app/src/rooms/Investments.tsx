/** The Investments room: positions valued at the latest prices on file,
 * the allocation donut against the declared targets, dividends YTD, and
 * the contribution machine. */
import { useCallback } from 'react'
import { api } from '../api'
import { EChart } from '../charts/EChart'
import type { EChartsCoreOption } from '../charts/echarts'
import { FONT, baseOption, tipHtml } from '../charts/options'
import { Card, CardSkeleton, Empty, LoadError } from '../components/ui'
import { cssv } from '../theme'
import type { Investments } from '../types'
import { useFetch } from '../useFetch'

const DONUT_VARS = ['--map-1', '--map-2', '--map-3', '--map-4', '--map-5', '--map-6']

export function InvestmentsRoom() {
  const { data, error, loading, reload } = useFetch('investments', api.investments)
  if (loading) return <div className="grid g-nwt"><CardSkeleton lines={7} /><CardSkeleton lines={5} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null
  return <InvestBody data={data} />
}

interface DonutParams {
  name: string
  dataIndex: number
}

function InvestBody({ data }: { data: Investments }) {
  const alloc = data.allocation

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

  return (
    <>
      <div className="grid g-nwt">
        <div className="sidecol">
        <Card
          k="Positions"
          sub="Every holding, valued at the latest price on file — the same numbers the reports carry."
          window={data.window}
        >
          {data.positions.length === 0 ? (
            <Empty><b>No holdings on file.</b> Import a brokerage statement and the table fills in.</Empty>
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
          <p className="goalfoot">Invested total <b className="num">{data.invested_total}</b> at the latest prices on file.</p>
          {data.paper_note && <p className="goalfoot">{data.paper_note}</p>}
        </Card>
        <div className="grid g-half" style={{ marginTop: 0 }}>
          <Card k="Dividends" window={data.dividends.window}>
            <div className="heromini num">{data.dividends.ytd}</div>
            <div className="herolab">
              {data.dividends.note || `${data.dividends.count} payment${data.dividends.count === 1 ? '' : 's'} booked this year`}
            </div>
          </Card>
          <Card k="Contributions" window={data.contributions.window}>
            <div className="heromini num">{data.contributions.bought}</div>
            <div className="herolab">{data.contributions.note}</div>
          </Card>
        </div>
        </div>
        <div className="sidecol">
          <Card k="The mix" sub={alloc ? undefined : 'Declared targets light this up.'}
            window={alloc ? `scored over ${alloc.invested}` : undefined}>
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
