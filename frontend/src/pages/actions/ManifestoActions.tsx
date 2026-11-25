import { useState } from 'react'
import { api } from '../../api/client'
import { guildKey, useGuildId } from '../../api/hooks'
import type { Schemas } from '../../api/types'
import { ActionButton, ConfirmDialog, useAction } from '../../components/ConfirmDialog'
import { MemberName } from '../../components/Names'
import { Button, Field, inputClass } from '../../components/ui'

type Done = Schemas['Done']

function WisdomField({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  return (
    <Field label="Wisdom" hint={`${value.length}/1000`}>
      <textarea className={`${inputClass} h-24`} maxLength={1000} value={value} onChange={(e) => onChange(e.target.value)} />
    </Field>
  )
}

export function AddWisdom({ authorId }: { authorId: string }) {
  const guildId = useGuildId()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const add = useAction(() => api<Done>('POST', `/api/guilds/${guildId}/manifestos/${authorId}/wisdom`, { text }), {
    invalidate: [guildKey(guildId)],
    success: (r) => r.message,
    onDone: () => {
      setOpen(false)
      setText('')
    },
  })
  return (
    <div className="mt-3">
      <Button onClick={() => setOpen(true)}>Add wisdom</Button>
      <ConfirmDialog
        open={open}
        title="Add to the manifesto"
        confirmLabel="Add it"
        busy={add.isPending}
        canConfirm={text.trim() !== ''}
        onConfirm={() => add.mutate()}
        onCancel={() => setOpen(false)}
      >
        <p>
          It goes at the end of <MemberName id={authorId} link={false} />
          's manifesto, as if they'd added it with /manifesto add, so they can edit or remove it. Nothing is posted in Discord.
        </p>
        <WisdomField value={text} onChange={setText} />
      </ConfirmDialog>
    </div>
  )
}

/** Edit and remove, for lines added with /manifesto add. File lines have none. */
export function LineActions({ authorId, line }: { authorId: string; line: Schemas['LineOut'] }) {
  const guildId = useGuildId()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(line.text)
  const path = `/api/guilds/${guildId}/manifestos/${authorId}/wisdom/${line.index}`
  const edit = useAction(() => api<Done>('PATCH', path, { text }), {
    invalidate: [guildKey(guildId)],
    success: (r) => r.message,
    onDone: () => setOpen(false),
  })
  return (
    <span className="inline-flex gap-1 opacity-0 group-hover:opacity-100">
      <Button
        variant="ghost"
        onClick={() => {
          setText(line.text)
          setOpen(true)
        }}
      >
        Edit
      </Button>
      <ActionButton
        label="Remove"
        variant="ghost"
        danger
        title={`Remove wisdom #${line.index}`}
        description={<p>“{line.text}” is taken back. Later lines move up a number. Nothing is posted in Discord.</p>}
        confirmLabel="Remove"
        run={() => api<Done>('DELETE', path)}
        invalidate={[guildKey(guildId)]}
        success={(r) => r.message}
      />
      <ConfirmDialog
        open={open}
        title={`Edit wisdom #${line.index}`}
        confirmLabel="Save"
        busy={edit.isPending}
        canConfirm={text.trim() !== '' && text !== line.text}
        onConfirm={() => edit.mutate()}
        onCancel={() => setOpen(false)}
      >
        <WisdomField value={text} onChange={setText} />
        <p className="text-stone-600">Nothing is posted in Discord.</p>
      </ConfirmDialog>
    </span>
  )
}
