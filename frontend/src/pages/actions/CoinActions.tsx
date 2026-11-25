import { useState } from 'react'
import { api } from '../../api/client'
import { guildKey, useGuildId } from '../../api/hooks'
import type { Schemas } from '../../api/types'
import { ConfirmDialog, useAction } from '../../components/ConfirmDialog'
import { MemberName } from '../../components/Names'
import { isId, MemberPicker } from '../../components/Pickers'
import { Button, Field, inputClass } from '../../components/ui'

type Mode = 'give' | 'take'

/** Give or take a member's coins, with the house on the other side. */
export function CoinActions({ userId, compact = false }: { userId?: string; compact?: boolean }) {
  const guildId = useGuildId()
  const [mode, setMode] = useState<Mode | null>(null)
  const [member, setMember] = useState(userId ?? '')
  const [amount, setAmount] = useState('')
  const [shadow, setShadow] = useState(false)
  const target = userId ?? member
  const action = useAction(
    () => api<Schemas['PocketResult']>('POST', `/api/guilds/${guildId}/coins/${mode}`, { user_id: target, amount, shadow }),
    { invalidate: [guildKey(guildId)], success: (r) => r.message, onDone: () => setMode(null) },
  )
  const open = (next: Mode) => {
    setAmount('')
    setShadow(false)
    setMode(next)
  }
  return (
    <>
      <span className="inline-flex gap-1">
        <Button variant={compact ? 'ghost' : 'primary'} onClick={() => open('give')}>
          Give
        </Button>
        <Button variant={compact ? 'ghost' : 'default'} onClick={() => open('take')}>
          Take
        </Button>
      </span>
      <ConfirmDialog
        open={mode !== null}
        title={mode === 'give' ? 'Give coins from the house' : 'Take coins for the house'}
        confirmLabel={mode === 'give' ? 'Give' : 'Take'}
        danger={mode === 'take'}
        busy={action.isPending}
        canConfirm={isId(target) && amount.trim() !== ''}
        onConfirm={() => action.mutate()}
        onCancel={() => setMode(null)}
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
        <Field label="Amount" hint='Like "5" or "3 cheddar", the same as the slash commands.'>
          <input className={inputClass} value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus />
        </Field>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={shadow} onChange={(e) => setShadow(e.target.checked)} /> ShadowBeans instead of their regular balance
        </label>
        <p className="text-stone-600">
          {mode === 'give'
            ? 'The house pays it, so the house balance goes down by the same amount.'
            : 'It goes to the house. If they have less than this, nothing happens.'}{' '}
          The ledger records it with you as the actor. The member isn't told, and nothing is posted in Discord.
        </p>
      </ConfirmDialog>
    </>
  )
}
