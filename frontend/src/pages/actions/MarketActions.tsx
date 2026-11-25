import { useState } from 'react'
import { api } from '../../api/client'
import { guildKey, useGuildId } from '../../api/hooks'
import type { Schemas } from '../../api/types'
import { ActionButton, ConfirmDialog, useAction } from '../../components/ConfirmDialog'
import { Button, Field, inputClass } from '../../components/ui'

type Done = Schemas['Done']

function HouseBet({ market }: { market: Schemas['MarketRowOut'] }) {
  const guildId = useGuildId()
  const [open, setOpen] = useState(false)
  const [choice, setChoice] = useState(market.choices[0] ?? '')
  const [amount, setAmount] = useState('')
  const bet = useAction(() => api<Done>('POST', `/api/guilds/${guildId}/markets/${market.thread_id}/house-bet`, { choice, amount }), {
    invalidate: [guildKey(guildId)],
    success: (r) => r.message,
    onDone: () => setOpen(false),
  })
  return (
    <>
      <Button onClick={() => setOpen(true)}>House bet</Button>
      <ConfirmDialog
        open={open}
        title="Bet for the house"
        confirmLabel="Place the bet"
        busy={bet.isPending}
        canConfirm={!!choice && amount.trim() !== ''}
        onConfirm={() => bet.mutate()}
        onCancel={() => setOpen(false)}
      >
        <Field label="Choice">
          <select className={inputClass} value={choice} onChange={(e) => setChoice(e.target.value)}>
            {market.choices.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        <Field label="Amount" hint='Like "5" or "3 cheddar". The house may go negative.'>
          <input className={inputClass} value={amount} onChange={(e) => setAmount(e.target.value)} />
        </Field>
        <p className="text-stone-600">
          The house's money goes into the pool. Betting on the same choice again adds to its bet; a different choice refunds the old bet and
          replaces it. This posts in the thread that the bot has joined in, without saying what it bet. Members find out when it resolves.
        </p>
      </ConfirmDialog>
    </>
  )
}

/** The actions on a market. Resolving stays in Discord (/bet resolve). */
export function MarketActions({ market }: { market: Schemas['MarketRowOut'] }) {
  const guildId = useGuildId()
  const base = `/api/guilds/${guildId}/markets/${market.thread_id}`
  const invalidate = [guildKey(guildId)]
  const success = (r: Done) => r.message
  return (
    <>
      {market.status === 'open' && <HouseBet market={market} />}
      {market.status === 'open' && (
        <ActionButton
          label="Close betting"
          title="Close betting"
          description={<p>No more bets can be placed. Nobody wins yet: resolve it from Discord with /bet resolve. This posts "Betting has been closed." in the thread.</p>}
          run={() => api<Done>('POST', `${base}/close`)}
          invalidate={invalidate}
          success={success}
        />
      )}
      {(market.status === 'open' || market.status === 'closed') && (
        <ActionButton
          label="Cancel"
          danger
          title="Cancel the market"
          description={<p>Every bet is refunded, the house's included, and the market can't be resolved. This posts "Prediction canceled. All bets have been refunded." in the thread.</p>}
          confirmLabel="Cancel the market"
          run={() => api<Done>('POST', `${base}/cancel`)}
          invalidate={invalidate}
          success={success}
        />
      )}
      {market.status === 'resolved' && (
        <ActionButton
          label="Nullify"
          danger
          variant="danger"
          title="Nullify this market"
          description={
            <p>
              Everything the market paid out is taken back, even if that puts winners in debt, and every stake is refunded, as if it had been
              canceled. This posts the list of who gives back and who gets back in the thread, without saying who did it.
            </p>
          }
          confirmLabel="Nullify"
          run={() => api<Done>('POST', `${base}/nullify`)}
          invalidate={invalidate}
          success={success}
        />
      )}
    </>
  )
}
