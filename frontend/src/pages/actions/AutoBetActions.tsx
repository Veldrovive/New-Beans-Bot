import { api } from '../../api/client'
import { guildKey, useGuildId } from '../../api/hooks'
import type { Schemas } from '../../api/types'
import { ActionButton } from '../../components/ConfirmDialog'

export function PostAutoBetButton() {
  const guildId = useGuildId()
  return (
    <ActionButton
      label="Post a bet now"
      variant="primary"
      title="Post an automatic bet"
      description={
        <p>
          This posts in Discord: the bot opens a new market in the gambling forum from a bet nobody has seen this round, on top of the daily one,
          and may place its own secret bet on it.
        </p>
      }
      confirmLabel="Post it"
      run={() => api<Schemas['Posted']>('POST', `/api/guilds/${guildId}/auto-bets/post`)}
      invalidate={[guildKey(guildId)]}
      success={(r) => r.message}
    />
  )
}
