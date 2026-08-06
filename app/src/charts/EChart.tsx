/** One chart container: init on mount, rebuild on theme change (the
 * options close over live CSS tokens), resize with its box, dispose on
 * unmount. `build` returns null to render nothing (empty states are the
 * caller's job). */
import { useEffect, useRef } from 'react'
import { useThemeEpoch } from '../theme'
import { echarts } from './echarts'
import type { EChartsCoreOption } from './echarts'

interface Props {
  className: string
  build: (width: number) => EChartsCoreOption | null
  onEvent?: [string, (params: unknown) => void]
  ariaLabel?: string
}

export function EChart({ className, build, onEvent, ariaLabel }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const epoch = useThemeEpoch()

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const option = build(el.clientWidth)
    if (!option) return
    const chart = echarts.init(el, null, { renderer: 'svg' })
    chart.setOption(option)
    if (onEvent) chart.on(onEvent[0], onEvent[1])
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.dispose()
    }
    // build closures capture room state; epoch re-reads CSS tokens
  }, [build, onEvent, epoch])

  return <div ref={ref} className={className} role="img" aria-label={ariaLabel} />
}
