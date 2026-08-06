/** Shared ECharts option builders — a faithful port of Sara Home's chart
 * chrome (tools/home.py JS): CSS-token colors read at build time, y-axis
 * labels precomputed by Python, tooltips rendered from server strings.
 * No money math happens here; strings pass through untouched. */
import { cssv } from '../theme'
import type { Tip, YAxis } from '../types'

export const FONT = "'Inter',system-ui,-apple-system,'Segoe UI',sans-serif"

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function tipHtml(tip: Tip): string {
  let h = `<div style='font-size:11.5px;color:${cssv('--muted')}'>${esc(tip.t)}</div>`
  for (const [value, label] of tip.rows) {
    h += `<div><b style='font-variant-numeric:tabular-nums'>${esc(value)}</b> `
      + `<span style='color:${cssv('--ink-2')};font-size:12px'>${esc(label)}</span></div>`
  }
  return h
}

export function baseOption(): Record<string, unknown> {
  return {
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: cssv('--surface'),
      borderColor: cssv('--border-strong'),
      borderWidth: 1,
      padding: [7, 11],
      textStyle: { color: cssv('--ink'), fontSize: 12.5, fontFamily: FONT },
      axisPointer: { lineStyle: { color: cssv('--axis') } },
      extraCssText: 'border-radius:8px;box-shadow:none;',
    },
    animation: false, // a statement page; money that counts up erodes trust
    textStyle: { fontFamily: FONT },
  }
}

export function catAxis(labels: string[], interval: number, width: number): Record<string, unknown> {
  const iv = width && width < 520 ? interval * 2 + 1 : interval
  return {
    type: 'category',
    data: labels,
    boundaryGap: false,
    axisLine: { lineStyle: { color: cssv('--axis') } },
    axisTick: { show: false },
    axisLabel: { color: cssv('--muted'), fontSize: 11, interval: iv, fontFamily: FONT },
  }
}

/** y ticks are Python-computed (min/step/labels): no tick math client-side. */
export function valAxis(y: YAxis): Record<string, unknown> {
  return {
    type: 'value',
    min: y.min,
    max: y.max,
    interval: y.step,
    axisLabel: {
      color: cssv('--muted'),
      fontSize: 11,
      fontFamily: FONT,
      formatter: (v: number) => y.labels[String(v)] ?? '',
    },
    splitLine: { lineStyle: { color: cssv('--grid'), width: 1 } },
    axisLine: { show: false },
    axisTick: { show: false },
  }
}

export function legendBox(): Record<string, unknown> {
  return {
    show: true, left: 0, top: 0, itemGap: 18, itemWidth: 22, itemHeight: 10,
    textStyle: { color: cssv('--ink-2'), fontSize: 12, fontFamily: FONT },
    inactiveColor: cssv('--muted'),
    selectedMode: true,
  }
}

export function nowDot(xy: [number, number], label: string, side: string): Record<string, unknown> {
  return {
    type: 'scatter', data: [xy], symbolSize: 9, legendHoverLink: false,
    itemStyle: { color: cssv('--accent'), borderColor: cssv('--surface'), borderWidth: 2 },
    label: {
      show: true, position: side, formatter: label,
      color: cssv('--ink'), fontWeight: 600, fontSize: 12.5, fontFamily: FONT,
    },
    tooltip: { show: false },
    z: 3,
  }
}

/** The soft area under the hero lines — accent at low alpha. */
export function areaGrad(varName: string, alpha: number): Record<string, unknown> {
  const color = cssv(varName)
  return {
    type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
    colorStops: [
      { offset: 0, color: withAlpha(color, alpha) },
      { offset: 1, color: withAlpha(color, 0) },
    ],
  }
}

function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(color)
  if (!m || !m[1]) return color
  const hex = m[1]
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}
