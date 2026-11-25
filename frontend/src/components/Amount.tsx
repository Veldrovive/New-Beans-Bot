import type { Amount as AmountType } from '../api/types'

export function Amount({ value, className = '' }: { value: AmountType | null | undefined; className?: string }) {
  if (!value) return <span className="text-stone-400">-</span>
  return (
    <span title={`${value.value} base units`} className={`whitespace-nowrap tabular-nums ${value.value < 0 ? 'text-red-700' : ''} ${className}`}>
      {value.text}
    </span>
  )
}
