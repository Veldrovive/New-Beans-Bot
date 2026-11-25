import { useId } from 'react'
import { useDirectory, useGuildId } from '../api/hooks'
import { inputClass } from './ui'

/** A member by ID. Suggests names from the directory; a raw ID works too when the bot isn't connected. */
export function MemberPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const listId = useId()
  const directory = useDirectory(useGuildId()).data
  const members = Object.entries(directory?.members ?? {}).filter(([, m]) => !m.bot)
  const known = directory?.members[value]
  return (
    <>
      <input
        className={inputClass}
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value.trim())}
        placeholder={members.length ? 'Start typing a name, or paste an ID' : 'Discord user ID'}
      />
      <datalist id={listId}>
        {members.map(([id, m]) => (
          <option key={id} value={id}>
            {m.display_name} ({m.name})
          </option>
        ))}
      </datalist>
      {known && <span className="mt-1 block text-xs text-stone-500">{known.display_name}</span>}
    </>
  )
}

/** A text channel or thread to speak in. Falls back to typing an ID. */
export function ChannelPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const directory = useDirectory(useGuildId()).data
  const channels = Object.entries(directory?.channels ?? {})
    .filter(([, c]) => c.type === 'text' || c.type === 'thread' || c.type === 'news')
    .sort(([, a], [, b]) => a.name.localeCompare(b.name))
  if (channels.length === 0) {
    return <input className={inputClass} value={value} onChange={(e) => onChange(e.target.value.trim())} placeholder="Channel ID" />
  }
  return (
    <select className={inputClass} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Pick a channel</option>
      {channels.map(([id, c]) => (
        <option key={id} value={id}>
          {c.type === 'thread' ? '  ↳ ' : '#'}
          {c.name}
        </option>
      ))}
    </select>
  )
}

export const isId = (value: string) => /^\d{1,20}$/.test(value)
