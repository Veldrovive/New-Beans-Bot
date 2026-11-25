import { useMemo, useState, type ReactNode } from 'react'
import { Button, Empty } from './ui'

export type Column<T> = {
  key: string
  header: ReactNode
  render: (row: T) => ReactNode
  /** Makes the column sortable (within the rows shown). */
  sort?: (row: T) => string | number | null | undefined
  className?: string
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  empty = 'Nothing here yet.',
  initialSort,
  rowClassName,
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  empty?: ReactNode
  initialSort?: { key: string; desc?: boolean }
  rowClassName?: (row: T) => string
}) {
  const [sort, setSort] = useState(initialSort)
  const sorted = useMemo(() => {
    const column = columns.find((c) => c.key === sort?.key)
    if (!column?.sort) return rows
    const value = column.sort
    return [...rows].sort((a, b) => {
      const x = value(a)
      const y = value(b)
      const order = x === y ? 0 : x === null || x === undefined ? 1 : y === null || y === undefined ? -1 : x < y ? -1 : 1
      return sort?.desc ? -order : order
    })
  }, [rows, columns, sort])

  if (rows.length === 0) return <Empty>{empty}</Empty>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-stone-200 text-left text-xs tracking-wide text-stone-500 uppercase">
            {columns.map((c) => (
              <th key={c.key} className={`px-2 py-2 font-medium ${c.className ?? ''}`}>
                {c.sort ? (
                  <button
                    className="uppercase hover:text-stone-900"
                    onClick={() => setSort((s) => ({ key: c.key, desc: s?.key === c.key ? !s.desc : true }))}
                  >
                    {c.header}
                    {sort?.key === c.key ? (sort.desc ? ' ▼' : ' ▲') : ''}
                  </button>
                ) : (
                  c.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={rowKey(row)} className={`border-b border-stone-100 last:border-0 hover:bg-stone-50 ${rowClassName?.(row) ?? ''}`}>
              {columns.map((c) => (
                <td key={c.key} className={`px-2 py-1.5 align-middle ${c.className ?? ''}`}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Pagination({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit) return null
  const page = Math.floor(offset / limit) + 1
  const pages = Math.ceil(total / limit)
  return (
    <div className="mt-3 flex items-center justify-end gap-2 text-sm">
      <span className="text-stone-500">
        Page {page} of {pages} ({total} rows)
      </span>
      <Button disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Previous
      </Button>
      <Button disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
        Next
      </Button>
    </div>
  )
}

/** Offset state for a paged list, reset when any of `deps` change. */
export function usePaging(limit = 50, deps: unknown[] = []) {
  const [state, setState] = useState({ offset: 0, deps: JSON.stringify(deps) })
  const key = JSON.stringify(deps)
  const offset = state.deps === key ? state.offset : 0
  return { limit, offset, setOffset: (next: number) => setState({ offset: next, deps: key }) }
}
