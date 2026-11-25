import { api } from '../../api/client'
import { guildKey, useGuildId } from '../../api/hooks'
import type { Schemas } from '../../api/types'
import { ActionButton } from '../../components/ConfirmDialog'
import { MemberName } from '../../components/Names'

type Done = Schemas['Done']

export function EndPassButton({ pass }: { pass: Schemas['PassOut'] }) {
  const guildId = useGuildId()
  return (
    <ActionButton
      label="End now"
      variant="ghost"
      title="End this day pass"
      description={
        <p>
          <MemberName id={pass.user_id} link={false} />
          's pass ends now, with no refund. The role comes off by itself within a minute. Nothing is announced.
        </p>
      }
      confirmLabel="End the pass"
      danger
      run={() => api<Done>('POST', `/api/guilds/${guildId}/vip/passes/${pass.id}/end`)}
      invalidate={[guildKey(guildId)]}
      success={(r) => r.message}
    />
  )
}

export function RevokeOnePercentButton({ holderId }: { holderId: string }) {
  const guildId = useGuildId()
  return (
    <ActionButton
      label="Revoke"
      variant="danger"
      title="Take away the 1%"
      description={
        <p>
          <MemberName id={holderId} link={false} /> loses the title with no refund. The price resets to the starting price, and the henchman goes
          too. The roles come off by themselves within a minute. Nothing is announced.
        </p>
      }
      confirmLabel="Revoke the title"
      danger
      run={() => api<Done>('POST', `/api/guilds/${guildId}/vip/one-percent/revoke`)}
      invalidate={[guildKey(guildId)]}
      success={(r) => r.message}
    />
  )
}
