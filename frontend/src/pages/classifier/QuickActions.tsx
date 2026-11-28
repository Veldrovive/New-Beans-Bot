import { useState } from 'react'
import { useAction } from '../../components/ConfirmDialog'
import { Button } from '../../components/ui'
import { changeImages, CLASSIFIER, type Change } from './api'
import { LabelInput } from './LabelInput'

/** Relabel, remove or restore one or more photos. Undoable, so no confirm step. */
export function QuickActions({
  ids,
  labels,
  removed = false,
  onDone,
  compact = false,
}: {
  ids: number[]
  labels: string[]
  removed?: boolean
  onDone?: () => void
  compact?: boolean
}) {
  const [label, setLabel] = useState('')
  const action = useAction((change: Change) => changeImages(ids, change), {
    invalidate: [CLASSIFIER],
    success: (r) => `${r.changed} photo${r.changed === 1 ? '' : 's'} changed.`,
    onDone: () => {
      setLabel('')
      onDone?.()
    },
  })
  const run = (change: Change) => action.mutate(change)
  const disabled = ids.length === 0 || action.isPending
  return (
    <div className={`flex flex-wrap items-center gap-2 ${compact ? 'text-xs' : ''}`}>
      <form
        className="flex items-center gap-1"
        onSubmit={(e) => {
          e.preventDefault()
          if (label.trim()) run({ label: label.trim() })
        }}
      >
        <div className="w-40">
          <LabelInput value={label} onChange={setLabel} labels={labels} />
        </div>
        <Button type="submit" disabled={disabled || !label.trim()}>
          Move to label
        </Button>
      </form>
      {removed ? (
        <Button disabled={disabled} onClick={() => run({ removed: false })}>
          Restore
        </Button>
      ) : (
        <Button variant="danger" disabled={disabled} onClick={() => run({ removed: true })}>
          Remove
        </Button>
      )}
    </div>
  )
}
