import { useState } from 'react'
import { api } from '../../api/client'
import { useGuildId } from '../../api/hooks'
import type { Schemas } from '../../api/types'
import { ActionButton, ConfirmDialog, useAction } from '../../components/ConfirmDialog'
import { MemberName } from '../../components/Names'
import { ChannelName } from '../../components/Names'
import { ChannelPicker, isId, MemberPicker } from '../../components/Pickers'
import { Button, Card, Field, inputClass } from '../../components/ui'

function MessageBox({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  return (
    <Field label="Message" hint={`${value.length}/2000. Members can be pinged; @everyone and roles can't.`}>
      <textarea className={`${inputClass} h-32`} maxLength={2000} value={value} onChange={(e) => onChange(e.target.value)} />
    </Field>
  )
}

/** The "Speak as the bot" card on the Overview page. */
export function SpeakCard() {
  const guildId = useGuildId()
  const [channel, setChannel] = useState('')
  const [message, setMessage] = useState('')
  const [confirming, setConfirming] = useState(false)
  const say = useAction(() => api<Schemas['Said']>('POST', `/api/guilds/${guildId}/admin/say`, { channel_id: channel, message }), {
    invalidate: [],
    success: (r) => r.message,
    onDone: () => {
      setConfirming(false)
      setMessage('')
    },
  })
  return (
    <Card title="Speak as the bot" actions={<DmButton />}>
      <div className="space-y-3">
        <Field label="Channel">
          <ChannelPicker value={channel} onChange={setChannel} />
        </Field>
        <MessageBox value={message} onChange={setMessage} />
        <Button variant="primary" disabled={!isId(channel) || !message.trim()} onClick={() => setConfirming(true)}>
          Say it
        </Button>
      </div>
      <ConfirmDialog
        open={confirming}
        title="Post as the bot"
        confirmLabel="Post it"
        busy={say.isPending}
        onConfirm={() => say.mutate()}
        onCancel={() => setConfirming(false)}
      >
        <p>
          This posts in Discord, in <ChannelName id={channel} />, as the bot. Nobody sees that you sent it.
        </p>
        <blockquote className="rounded-md bg-stone-100 p-2 whitespace-pre-wrap">{message}</blockquote>
      </ConfirmDialog>
    </Card>
  )
}

/** DM a member as the bot. With `userId`, for that member; otherwise with a member picker. */
export function DmButton({ userId, compact = false }: { userId?: string; compact?: boolean }) {
  const guildId = useGuildId()
  const [open, setOpen] = useState(false)
  const [member, setMember] = useState('')
  const [message, setMessage] = useState('')
  const target = userId ?? member
  const dm = useAction(() => api<Schemas['Done']>('POST', `/api/guilds/${guildId}/admin/dm`, { user_id: target, message }), {
    invalidate: [],
    success: (r) => r.message,
    onDone: () => {
      setOpen(false)
      setMessage('')
    },
  })
  return (
    <>
      <Button variant={compact ? 'ghost' : 'default'} onClick={() => setOpen(true)}>
        {compact ? 'DM' : 'DM a member'}
      </Button>
      <ConfirmDialog
        open={open}
        title="DM as the bot"
        confirmLabel="Send the DM"
        busy={dm.isPending}
        canConfirm={isId(target) && message.trim() !== ''}
        onConfirm={() => dm.mutate()}
        onCancel={() => setOpen(false)}
      >
        {userId ? (
          <p>
            To: <MemberName id={userId} link={false} />
          </p>
        ) : (
          <Field label="To">
            <MemberPicker value={member} onChange={setMember} />
          </Field>
        )}
        <MessageBox value={message} onChange={setMessage} />
        <p className="text-stone-600">This sends a DM in Discord from the bot. They won't know it was you.</p>
      </ConfirmDialog>
    </>
  )
}

export function SyncButton() {
  return (
    <ActionButton
      label="Sync commands"
      title="Sync slash commands"
      description={<p>Re-registers every slash command with Discord, like /admin sync. Discord rate-limits this, so don't spam it.</p>}
      confirmLabel="Sync"
      run={() => api<Schemas['Synced']>('POST', '/api/admin/sync')}
      invalidate={[['meta']]}
      success={(r) => r.message}
    />
  )
}
