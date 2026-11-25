import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { ApiError } from '../api/client'

export function Button({
  variant = 'default',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default' | 'primary' | 'danger' | 'ghost' }) {
  const styles = {
    default: 'border border-stone-300 bg-white hover:bg-stone-50',
    primary: 'bg-amber-700 text-white hover:bg-amber-800',
    danger: 'bg-red-700 text-white hover:bg-red-800',
    ghost: 'hover:bg-stone-200',
  }[variant]
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    />
  )
}

export function Card({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-stone-200 bg-white p-4 shadow-sm ${className}`}>
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="font-semibold">{title}</h2>}
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h1 className="text-2xl font-bold">{title}</h1>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}

const BADGE = {
  stone: 'bg-stone-200 text-stone-800',
  green: 'bg-green-100 text-green-800',
  red: 'bg-red-100 text-red-800',
  amber: 'bg-amber-100 text-amber-900',
  blue: 'bg-sky-100 text-sky-800',
  purple: 'bg-purple-100 text-purple-800',
}

export function Badge({ color = 'stone', children, title }: { color?: keyof typeof BADGE; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap ${BADGE[color]}`}>
      {children}
    </span>
  )
}

export function Loading() {
  return <p className="text-sm text-stone-500">Loading...</p>
}

export function ErrorBox({ error }: { error: unknown }) {
  const text = error instanceof ApiError ? error.message : String(error)
  return <p className="rounded-md bg-red-50 p-3 text-sm text-red-800">{text}</p>
}

/** Renders loading and error states, then the children once the data is there. */
export function Loaded<T>({ query, children }: { query: { data?: T; error: unknown; isPending: boolean }; children: (data: T) => ReactNode }) {
  if (query.error) return <ErrorBox error={query.error} />
  if (query.isPending || query.data === undefined) return <Loading />
  return <>{children(query.data)}</>
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-4 text-center text-sm text-stone-500">{children}</p>
}

export function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-xs tracking-wide text-stone-500 uppercase">{label}</div>
      <div className="text-lg font-semibold break-words [&_*]:whitespace-normal">{children}</div>
    </div>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-stone-500">{hint}</span>}
    </label>
  )
}

export const fieldClass = 'rounded-md border border-stone-300 bg-white px-2 py-1 text-sm focus:border-amber-600 focus:outline-none'
export const inputClass = 'w-full rounded-md border border-stone-300 bg-white px-2 py-1 text-sm focus:border-amber-600 focus:outline-none'
