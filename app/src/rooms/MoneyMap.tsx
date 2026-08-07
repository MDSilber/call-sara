/** The Money map room: where every liquid dollar sits (treemap with
 * drill-down), the net-worth line with why-it-moved attribution, and the
 * vs-the-thesis drift strip. Sums tie to the headline by construction. */
import { useCallback } from 'react'
import { api } from '../api'
import { EChart } from '../charts/EChart'
import type { EChartsCoreOption } from '../charts/echarts'
import { FONT, areaGrad, baseOption, catAxis, legendBox, nowDot, tipHtml, valAxis } from '../charts/options'
import { Card, CardSkeleton, Empty, LoadError, Track } from '../components/ui'
import { useOwner, ownerLabel } from '../ownerLens'
import { cssv } from '../theme'
import type { MapNode, Networth, OwnerMapNode } from '../types'
import { useFetch } from '../useFetch'

export function MoneyMapRoom(props: { onRegister: (account: string) => void }) {
  const owner = useOwner()
  const { data, error, loading, reload } = useFetch('networth', api.networth)
  if (loading) return <div className="grid g-nwt"><CardSkeleton lines={8} /><CardSkeleton lines={6} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null
  if (owner !== 'all') {
    return <OwnerMapBody owner={owner} household={data} onRegister={props.onRegister} />
  }
  return (
    <>
      <MapBody data={data} />
      <AccountsCard onRegister={props.onRegister} />
    </>
  )
}

/** The lens view: one person's institutions and accounts, every tile a
 * door into that account's register. Household-level cards (thesis, the
 * net-worth line) stay in the All view — they are the household's. */
function OwnerMapBody(props: {
  owner: string
  household: Networth
  onRegister: (account: string) => void
}) {
  const { owner } = props
  const { data, error, loading, reload } = useFetch(
    `ownermap:${owner}`, () => api.ownerMap(owner))

  const build = useCallback((width: number): EChartsCoreOption | null => {
    if (!data || data.tree.length === 0) return null
    void width
    const opt = baseOption()
    const tooltip = opt.tooltip as Record<string, unknown>
    tooltip.trigger = 'item'
    tooltip.formatter = (p: TreemapParams) => {
      const d = p.data ?? {}
      if (!d.amt) return ''
      const path = p.treePathInfo
        ? p.treePathInfo.map((x) => x.name).filter(Boolean).join(' › ')
        : (d.name ?? '')
      return tipHtml({ t: path, rows: [[d.amt, `${d.pct ?? ''} of ${ownerLabel(owner)}'s assets`]] })
    }
    const colored = data.tree.map((g, i) => ownerNode(g, cssv(OWNER_MAP_VARS[i % OWNER_MAP_VARS.length] ?? '--map-6')))
    opt.series = [{
      type: 'treemap', name: ownerLabel(owner),
      data: colored,
      roam: false, nodeClick: false, leafDepth: 2,
      left: 0, right: 0, top: 4, bottom: 8,
      breadcrumb: { show: false },
      label: {
        show: true, fontFamily: FONT, fontSize: 12,
        formatter: (p: TreemapParams) => {
          const d = p.data ?? {}
          return d.amt ? `${d.name ?? ''}\n${d.amt} · ${d.pct ?? ''}` : p.name
        },
      },
      itemStyle: { borderColor: cssv('--surface'), borderWidth: 2, gapWidth: 2 },
      levels: [
        { itemStyle: { borderWidth: 0, gapWidth: 3 } },
        {
          colorAlpha: [0.92, 1],
          itemStyle: { gapWidth: 2, borderWidth: 2, borderColorSaturation: 0.55 },
          upperLabel: { show: true, height: 22, fontFamily: FONT, fontSize: 11.5, fontWeight: 600, color: '#fff' },
        },
        { colorAlpha: [0.68, 0.88] },
      ],
      emphasis: { label: { show: true } },
    }]
    return opt as EChartsCoreOption
  }, [data, owner])

  if (loading) return <div className="grid g-solo"><CardSkeleton lines={8} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null

  const onClick = (params: unknown) => {
    const d = (params as { data?: { account?: string } }).data
    if (d?.account) props.onRegister(d.account)
  }

  return (
    <>
      <div className="grid g-solo">
        <Card
          k={`Where ${ownerLabel(owner)}'s money sits`}
          sub="Owned and joint accounts under this lens. Click an account tile to open its register."
          window={props.household.window}
        >
          {data.tree.length === 0 ? (
            <Empty><b>No accounts under {ownerLabel(owner)}.</b> Tag opens with owner metadata and the lens lights up.</Empty>
          ) : (
            <>
              <EChart className="chart chart-map" build={build}
                onEvent={['click', onClick]} ariaLabel={`asset treemap for ${owner}`} />
              <p className="mapcap">{data.caption} · net <b className="num">{data.net}</b></p>
              <p className="maphint">The thesis and the net-worth line are household-level — flip the lens to All for those.</p>
            </>
          )}
        </Card>
      </div>
      <AccountsCard onRegister={props.onRegister} />
    </>
  )
}

const OWNER_MAP_VARS = ['--map-1', '--map-3', '--map-5', '--map-2', '--map-4', '--map-6']

function ownerNode(n: OwnerMapNode, color: string | null): Record<string, unknown> {
  const out: Record<string, unknown> = { name: n.name, value: n.value, amt: n.amt, pct: n.pct }
  if (n.account) out.account = n.account
  if (color) out.itemStyle = { color }
  if (n.children) out.children = n.children.map((k) => ownerNode(k, null))
  return out
}

/** Every account with its latest balance — each row opens the register. */
function AccountsCard(props: { onRegister: (account: string) => void }) {
  const owner = useOwner()
  const { data, error, loading, reload } = useFetch(
    `accounts:${owner}`, () => api.accounts(owner).then((r) => r.accounts))
  if (loading) return <div className="grid g-solo"><CardSkeleton lines={5} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  const rows = (data ?? []).filter((a) => a.owner !== 'transit')
  if (rows.length === 0) return null
  return (
    <div className="grid g-solo">
      <Card k="Accounts" sub="Latest balances; open any statement." >
        <table className="regtable">
          <thead>
            <tr><th>Account</th><th className="rother">Owner</th><th className="r">Balance</th></tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.account} onClick={() => props.onRegister(a.account)}
                style={{ cursor: 'pointer' }}>
                <td>
                  <span className="rp">{a.account.split(':').slice(2).join(' · ') || a.account}</span>
                  <div className="rn">{a.account}{a.is_open ? '' : ' · closed'}</div>
                </td>
                <td className="rother">{a.owner ?? '—'}</td>
                <td className="r num"><b>{a.balance}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

interface TreemapParams {
  data?: { amt?: string; pct?: string; name?: string; own?: string }
  name: string
  treePathInfo?: { name: string }[]
}

function mapNode(n: MapNode, color: string | null): Record<string, unknown> {
  const out: Record<string, unknown> = { name: n.name, value: n.value, amt: n.amt, pct: n.pct }
  if (n.own) out.own = n.own
  if (color) out.itemStyle = { color }
  if (n.children) out.children = n.children.map((k) => mapNode(k, null))
  return out
}

function MapBody({ data }: { data: Networth }) {
  const buildNW = useCallback((width: number): EChartsCoreOption | null => {
    const N = data.chart
    if (!N) return null
    const opt = baseOption()
    const hasCost = N.atcost.some((v) => v !== null)
    const zoomBar = N.labels.length >= 10
    opt.grid = { left: 62, right: 84, top: hasCost ? 38 : 16, bottom: zoomBar ? 52 : 28 }
    if (hasCost) opt.legend = legendBox()
    const dataZoom: Record<string, unknown>[] = [
      { type: 'inside', zoomOnMouseWheel: 'shift', moveOnMouseWheel: false },
    ]
    if (zoomBar) {
      dataZoom.push({
        type: 'slider', height: 16, bottom: 4, brushSelect: false,
        borderColor: 'rgba(0,0,0,0)', backgroundColor: 'rgba(0,0,0,0)',
        fillerColor: cssv('--accent-soft'),
        handleStyle: { color: cssv('--surface'), borderColor: cssv('--axis') },
        moveHandleStyle: { color: cssv('--axis') },
        emphasis: { handleStyle: { borderColor: cssv('--accent') } },
        dataBackground: { lineStyle: { color: cssv('--grid') }, areaStyle: { color: cssv('--accent-soft') } },
        selectedDataBackground: { lineStyle: { color: cssv('--axis') }, areaStyle: { color: cssv('--accent-soft') } },
        textStyle: { color: cssv('--muted'), fontSize: 10, fontFamily: FONT },
      })
    }
    opt.dataZoom = dataZoom
    opt.xAxis = catAxis(N.labels, N.xint, width)
    opt.yAxis = valAxis(N.y)
    ;(opt.tooltip as Record<string, unknown>).formatter = (ps: { dataIndex: number }[]) => {
      const tip = N.tips[ps[0]?.dataIndex ?? 0]
      return tip ? tipHtml(tip) : ''
    }
    const market: Record<string, unknown> = {
      name: 'at market prices', type: 'line', data: N.market, symbol: 'none',
      color: cssv('--accent'),
      lineStyle: { width: 3, cap: 'round' },
      areaStyle: { color: areaGrad('--accent', 0.22) },
      emphasis: { disabled: true }, z: 2,
    }
    if (N.seam !== null) {
      market.markLine = {
        symbol: 'none', silent: true,
        lineStyle: { color: cssv('--axis'), type: 'dashed', width: 1 },
        label: { formatter: N.seamLabel, position: 'insideEndTop', color: cssv('--muted'), fontSize: 10.5, fontFamily: FONT },
        data: [{ xAxis: N.seam }],
      }
    }
    const series: Record<string, unknown>[] = [market]
    if (hasCost) {
      series.push({
        name: 'at cost (no dated price yet)', type: 'line', data: N.atcost,
        symbol: 'none', color: cssv('--accent'),
        lineStyle: { width: 2, type: 'dashed', opacity: 0.75 },
        emphasis: { disabled: true }, z: 1,
      })
    }
    series.push(nowDot(N.end.xy, N.end.label, 'right'))
    opt.series = series
    return opt as EChartsCoreOption
  }, [data.chart])

  const buildMap = useCallback((width: number): EChartsCoreOption | null => {
    const M = data.map
    if (!M) return null
    void width
    const opt = baseOption()
    const tooltip = opt.tooltip as Record<string, unknown>
    tooltip.trigger = 'item'
    tooltip.formatter = (p: TreemapParams) => {
      const d = p.data ?? {}
      if (!d.amt) return ''
      const path = p.treePathInfo
        ? p.treePathInfo.map((t) => t.name).filter(Boolean).join(' › ')
        : (d.name ?? '')
      const rows: [string, string][] = [[d.amt, `${d.pct ?? ''} of assets`]]
      if (d.own) rows.push([d.own, 'owner'])
      return tipHtml({ t: path, rows })
    }
    opt.series = [{
      type: 'treemap', name: 'everything',
      data: M.tree.map((g) => mapNode(g, g.cvar ? cssv(g.cvar) : null)),
      roam: false, nodeClick: 'zoomToNode', leafDepth: 2,
      left: 0, right: 0, top: 4, bottom: 34,
      breadcrumb: {
        show: true, left: 'center', bottom: 0, height: 22,
        itemStyle: {
          color: cssv('--surface-2'), borderColor: cssv('--border-strong'), borderWidth: 1,
          textStyle: { color: cssv('--ink-2'), fontFamily: FONT, fontSize: 11.5 },
        },
        emphasis: { itemStyle: { color: cssv('--accent-soft') } },
      },
      label: {
        show: true, fontFamily: FONT, fontSize: 12,
        formatter: (p: TreemapParams) => {
          const d = p.data ?? {}
          return d.amt ? `${d.name ?? ''}\n${d.amt} · ${d.pct ?? ''}` : p.name
        },
      },
      itemStyle: { borderColor: cssv('--surface'), borderWidth: 2, gapWidth: 2 },
      levels: [
        { itemStyle: { borderWidth: 0, gapWidth: 3 } },
        {
          colorAlpha: [0.92, 1],
          itemStyle: { gapWidth: 2, borderWidth: 2, borderColorSaturation: 0.55 },
          upperLabel: { show: true, height: 22, fontFamily: FONT, fontSize: 11.5, fontWeight: 600, color: '#fff' },
        },
        { colorAlpha: [0.68, 0.88] },
      ],
      emphasis: { label: { show: true } },
    }]
    return opt as EChartsCoreOption
  }, [data.map])

  return (
    <>
      <div className="grid g-nwt">
        <Card k="Liquid net worth" sub={data.headline.sub} window={data.headline.window}>
          <div className="kv num" style={{ fontSize: 32 }}>{data.headline.liquid}</div>
          <div className="chiprow">
            {data.headline.delta && (
              <span className={`chip ${data.headline.delta.cls}`}>{data.headline.delta.body}</span>
            )}
            {data.paper && <span className="chip">paper (not counted) <b>{data.paper}</b></span>}
          </div>
          {data.chart ? (
            <EChart className="chart chart-nw" build={buildNW} ariaLabel="liquid net worth by month" />
          ) : (
            <Empty><b>One month-end makes a dot; two make a line.</b> Keep importing.</Empty>
          )}
          {data.attribution && (
            <div className="attr">
              {data.attribution.rows.map((r) => (
                <div className="attr-row" key={r.window}>
                  {r.suppressed ? (
                    <p className="attr-sup">{r.suppressed}</p>
                  ) : (
                    <>
                      <p className="attr-line">
                        <b className={`num ${r.cls ?? ''}`}>{r.delta}</b>{' '}
                        <span className="attr-win">{r.window}</span> — {r.body}{r.note}
                      </p>
                      {r.segs && (
                        <div className="attrbar" aria-label={r.aria}>
                          {r.segs.map((s, i) => (
                            <span key={i} className={`attrseg ${s.cls}`} style={{ width: `${s.width}%` }} />
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card k="Vs the thesis" sub={data.thesis.sub} window={data.thesis.window}>
          {data.thesis.nudge ? (
            <p className="nudge">{data.thesis.nudge}</p>
          ) : (
            <>
              <div className="drift">
                {data.thesis.rows.map((r) => (
                  <div className="driftrow" key={r.label}>
                    <span className="dlabel">{r.label}</span>
                    <div className="dtrack">
                      <span className={`dfill ${r.state}`} style={{ width: `${r.fill}%` }} />
                      <span className="dtick" style={{ left: `${r.tick}%` }} />
                    </div>
                    <span className="dnum">
                      <b className={`num ${r.state}`}>{r.now}</b> · {r.value} · {r.target} {r.band}
                      {r.delta && <span className="ddelta"> · {r.delta}</span>}
                    </span>
                  </div>
                ))}
              </div>
              {data.thesis.notes && <p className="concline">{data.thesis.notes}</p>}
              {data.thesis.conc && <p className="concline">{data.thesis.conc}</p>}
            </>
          )}
          {data.milestones && (
            <>
              <div className="barwrap"><Track fill={data.milestones.pct.toFixed(1)} /></div>
              <div className="barnotes">
                <span>{data.milestones.label}</span>
                {data.milestones.crossed > 0 && (
                  <span>{data.milestones.crossed} crossed</span>
                )}
              </div>
            </>
          )}
        </Card>
      </div>
      {data.map && (
        <div className="grid g-solo">
          <Card k="Where every dollar sits" sub="Institution → account → holding. Click to zoom; the breadcrumb walks back." window={data.map.window}>
            <EChart className="chart chart-map" build={buildMap} ariaLabel="asset treemap" />
            <p className="mapcap">{data.map.caption}</p>
            {data.unpriced.length > 0 && (
              <p className="maphint">Unpriced (not counted): {data.unpriced.join(', ')}</p>
            )}
          </Card>
        </div>
      )}
    </>
  )
}
