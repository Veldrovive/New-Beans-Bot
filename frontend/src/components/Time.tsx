const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
  ['second', 1],
]

export function relativeTime(date: Date): string {
  const seconds = (date.getTime() - Date.now()) / 1000
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size || unit === 'second') return relative.format(Math.round(seconds / size), unit)
  }
  return ''
}

/** A time shown relative to now, with the exact local time on hover. */
export function Time({ value, empty = '-' }: { value: string | null | undefined; empty?: string }) {
  if (!value) return <span className="text-stone-400">{empty}</span>
  const date = new Date(value)
  return (
    <time dateTime={value} title={date.toLocaleString()} className="whitespace-nowrap">
      {relativeTime(date)}
    </time>
  )
}

/** A calendar date, as stored (no time zone). */
export function DateText({ value }: { value: string }) {
  const [year, month, day] = value.split('-').map(Number)
  return <span className="whitespace-nowrap">{new Date(year, month - 1, day).toLocaleDateString()}</span>
}
