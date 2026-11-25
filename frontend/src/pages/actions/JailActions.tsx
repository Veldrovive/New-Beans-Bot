import { useState } from 'react'
import { api } from '../../api/client'
import { guildKey, useGuildId } from '../../api/hooks'
import type { Schemas } from '../../api/types'
import { ActionButton, ConfirmDialog, useAction } from '../../components/ConfirmDialog'
import { MemberName } from '../../components/Names'
import { isId, MemberPicker } from '../../components/Pickers'
import { Button, Field } from '../../components/ui'

type Done = Schemas['Done']

/** "Jail a member" with a member picker, or for one member when `userId` is given. */
export function JailActions({ userId, compact = false }: { userId?: string; compact?: boolean }) {
  const guildId = useGuildId()
  const [open, setOpen] = useState(false)
  const [member, setMember] = useState('')
  const target = userId ?? member
  const action = useAction(() => api<Done>('POST', `/api/guilds/${guildId}/jail/imprison`, { user_id: target }), {
    invalidate: [guildKey(guildId)],
    success: (r) => r.message,
    onDone: () => setOpen(false),
  })
  return (
    <>
      <Button variant={compact ? 'ghost' : 'danger'} onClick={() => setOpen(true)}>
        {compact ? 'Jail' : 'Jail a member'}
      </Button>
      <ConfirmDialog
        open={open}
        title="Send to jail"
        confirmLabel="Jail them"
        danger
        busy={action.isPending}
        canConfirm={isId(target)}
        onConfirm={() => action.mutate()}
        onCancel={() => setOpen(false)}
      >
        {userId ? (
          <p>
            Member: <MemberName id={userId} link={false} />
          </p>
        ) : (
          <Field label="Member">
            <MemberPicker value={member} onChange={setMember} />
          </Field>
        )}
        <p className="text-stone-600">
          This gives them the jail role in Discord and starts a sentence of the configured length. The sentence belongs to the bot channel.
          Nothing is announced.
        </p>
      </ConfirmDialog>
    </>
  )
}

export function ReleaseButton({ userId }: { userId: string }) {
  const guildId = useGuildId()
  return (
    <ActionButton
      label="Release"
      variant="ghost"
      title="Release from jail"
      description={
        <p>
          <MemberName id={userId} link={false} /> is set free now: the sentence ends and the jail role comes off in Discord. Nothing is announced.
        </p>
      }
      confirmLabel="Release"
      run={() => api<Done>('POST', `/api/guilds/${guildId}/jail/release`, { user_id: userId })}
      invalidate={[guildKey(guildId)]}
      success={(r) => r.message}
    />
  )
}
