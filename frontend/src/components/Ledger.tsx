import type { Schemas } from '../api/types'
import { Amount } from './Amount'
import { DataTable } from './DataTable'
import { MemberName } from './Names'
import { Badge } from './ui'
import { Time } from './Time'
import { Link } from 'react-router-dom'
import { useGuildId } from '../api/hooks'

type Row = Schemas['LedgerRow']

function Side({ id, shadow }: { id: string | null; shadow: boolean }) {
  if (!id) return <span className="text-stone-400 italic">outside</span>
  return (
    <span className="inline-flex items-center gap-1">
      <MemberName id={id} />
      {shadow && <Badge color="purple">shadow</Badge>}
    </span>
  )
}

/** Ledger rows: who paid whom, how much, why and who caused it. */
export function LedgerTable({ rows }: { rows: Row[] }) {
  const guildId = useGuildId()
  return (
    <DataTable
      rows={rows}
      rowKey={(r) => String(r.id)}
      empty="No ledger rows match."
      columns={[
        { key: 'time', header: 'Time', render: (r) => <Time value={r.created_at} /> },
        { key: 'kind', header: 'Kind', render: (r) => <Badge>{r.kind}</Badge> },
        { key: 'from', header: 'From', render: (r) => <Side id={r.from_user_id} shadow={r.from_shadow} /> },
        { key: 'to', header: 'To', render: (r) => <Side id={r.to_user_id} shadow={r.to_shadow} /> },
        { key: 'amount', header: 'Amount', render: (r) => <Amount value={r.amount} />, className: 'text-right' },
        { key: 'actor', header: 'Actor', render: (r) => (r.actor_user_id ? <MemberName id={r.actor_user_id} /> : null) },
        {
          key: 'link',
          header: '',
          render: (r) =>
            r.thread_id ? (
              <Link to={`/g/${guildId}/markets/${r.thread_id}`}>market</Link>
            ) : r.message_id ? (
              // The ledger doesn't record the channel, so a message can't be linked, only named.
              <span className="font-mono text-xs text-stone-500" title="Message ID">
                msg {r.message_id}
              </span>
            ) : null,
        },
      ]}
    />
  )
}
