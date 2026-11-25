import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { guildKey, useDirectory, useGuildId } from '../api/hooks'
import { ErrorBox, Loading } from './ui'

/** A Discord ID in the config, with the name it points at when the directory knows it. */
function IdValue({ id }: { id: string }) {
  const directory = useDirectory(useGuildId()).data
  const name = directory?.members[id]?.display_name ?? (directory?.roles[id] ? `@${directory.roles[id].name}` : undefined) ?? (directory?.channels[id] ? `#${directory.channels[id].name}` : undefined)
  return (
    <span>
      <span className="font-mono text-xs">{id}</span>
      {name && <span className="ml-1 text-amber-800">({name})</span>}
    </span>
  )
}

const ID = /^\d{17,20}$/

function Tree({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-stone-400">none</span>
  if (typeof value === 'string') return ID.test(value) ? <IdValue id={value} /> : <span className="break-words whitespace-pre-wrap">{value}</span>
  if (typeof value === 'number' || typeof value === 'boolean') return <span className="font-mono text-xs">{String(value)}</span>
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-stone-400">[]</span>
    return (
      <ol className="list-decimal space-y-1 pl-5">
        {value.map((item, i) => (
          <li key={i}>
            <Tree value={item} />
          </li>
        ))}
      </ol>
    )
  }
  return (
    <dl className="space-y-1 border-l border-stone-200 pl-3">
      {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
        <div key={key}>
          <dt className="inline font-medium text-stone-600">{ID.test(key) ? <IdValue id={key} /> : key}: </dt>
          <dd className="inline">
            {item !== null && typeof item === 'object' ? (
              <div className="mt-1">
                <Tree value={item} />
              </div>
            ) : (
              <Tree value={item} />
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** The feature's config section, read-only. Config lives in the YAML files in git. */
export function ConfigPanel({ section }: { section: string }) {
  const guildId = useGuildId()
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: [...guildKey(guildId), 'config', section],
    queryFn: () => api<unknown>('GET', `/api/guilds/${guildId}/config/${section}`),
    enabled: open,
  })
  return (
    <details className="mt-6 rounded-xl border border-stone-200 bg-white p-4 text-sm" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="cursor-pointer font-semibold">Config: {section}</summary>
      <p className="mt-2 mb-3 text-xs text-stone-500">Read-only. Change it in configs/env/*.yaml and restart the bot.</p>
      {query.error ? <ErrorBox error={query.error} /> : query.isPending ? <Loading /> : <Tree value={query.data} />}
    </details>
  )
}
