import { useId } from 'react'
import { inputClass } from '../../components/ui'

/** A label: pick an existing one, or type a new one. The server lowercases it and turns spaces into underscores. */
export function LabelInput({ value, onChange, labels, autoFocus }: { value: string; onChange: (label: string) => void; labels: string[]; autoFocus?: boolean }) {
  const listId = useId()
  return (
    <>
      <input className={inputClass} list={listId} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Label" autoFocus={autoFocus} />
      <datalist id={listId}>
        {labels.map((l) => (
          <option key={l} value={l} />
        ))}
      </datalist>
    </>
  )
}
