/** The small shared vocabulary: cards, tracks, empty states, code spans. */
import type { ReactNode } from 'react'

export function Card(props: {
  k?: string
  sub?: string
  window?: string
  /** Tiny neutral chip beside the key — e.g. "household" under a person lens. */
  badge?: string
  className?: string
  children: ReactNode
}) {
  const { k, sub, window: win, badge, className, children } = props
  return (
    <section className={`card${className ? ` ${className}` : ''}`}>
      {(k || win) && (
        <div className="cardhead">
          <div>
            {k && (
              <div className="ck">
                {k}
                {badge && <span className="hbadge">{badge}</span>}
              </div>
            )}
            {sub && <div className="sub">{sub}</div>}
          </div>
          {win && <div className="window">{win}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

export function Track(props: { fill: string; cls?: string; trackCls?: string }) {
  return (
    <div className={`track${props.trackCls ? ` ${props.trackCls}` : ''}`}>
      <span
        className={`fill${props.cls ? ` ${props.cls}` : ''}`}
        style={{ width: `${props.fill}%` }}
      />
    </div>
  )
}

export function Empty(props: { children: ReactNode }) {
  return <p className="empty">{props.children}</p>
}

export function Skeleton(props: { h?: number; w?: string }) {
  return (
    <div
      className="skeleton"
      style={{ minHeight: props.h ?? 16, width: props.w ?? '100%' }}
      aria-hidden
    />
  )
}

export function CardSkeleton(props: { lines?: number }) {
  const n = props.lines ?? 4
  return (
    <section className="card" aria-busy="true" aria-label="loading">
      <Skeleton h={12} w="30%" />
      <div style={{ height: 12 }} />
      {Array.from({ length: n }, (_, i) => (
        <div key={i} style={{ marginTop: 10 }}>
          <Skeleton h={14} w={`${90 - i * 12}%`} />
        </div>
      ))}
    </section>
  )
}

export function LoadError(props: { error: string; retry?: () => void }) {
  return (
    <div className="loaderr" role="alert">
      Couldn&rsquo;t load this room: {props.error}{' '}
      {props.retry && (
        <button className="btn quiet" onClick={props.retry}>
          retry
        </button>
      )}
    </div>
  )
}

/** Server text keeps `code` in backticks; render them as real code spans. */
export function CodeText(props: { text: string }) {
  const parts = props.text.split(/`([^`]{1,80})`/)
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? <code key={i}>{part}</code> : <span key={i}>{part}</span>,
      )}
    </>
  )
}
