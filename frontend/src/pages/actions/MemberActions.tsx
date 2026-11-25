import { CoinActions } from './CoinActions'
import { DmButton } from './BotActions'
import { JailActions } from './JailActions'

/** The actions on a member's row of the Members table. */
export function MemberActions({ userId, jailed }: { userId: string; jailed: boolean }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      <CoinActions userId={userId} compact />
      <DmButton userId={userId} compact />
      {!jailed && <JailActions userId={userId} compact />}
    </span>
  )
}
