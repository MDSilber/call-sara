/** Ledger account paths have no place in Sara's sentences. Until every
 * producer is rewritten, strip any that slip through to their nickname
 * segment ('Assets:US:Vanguard:Brokerage' -> 'Brokerage'). */
const ACCOUNT_PATH = /\b(?:Assets|Liabilities|Income|Expenses|Equity):[A-Za-z0-9:_-]+/g

export function civil(text: string): string {
  return text.replace(ACCOUNT_PATH, (path) => {
    const segs = path.split(':').slice(1)
    const trimmed = segs[0] && /^[A-Z]{2}$/.test(segs[0]) ? segs.slice(1) : segs
    return trimmed[trimmed.length - 1] ?? path
  })
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug',
  'Sep', 'Oct', 'Nov', 'Dec']

/** '2026-09-06' -> 'Sep 6, 2026' — dates read like a person wrote them. */
export function friendlyDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) return iso
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`
}
