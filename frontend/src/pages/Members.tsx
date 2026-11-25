import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { Amount } from '../components/Amount'
import { DataTable } from '../components/DataTable'
import { MemberName } from '../components/Names'
import { Badge, Card, Loaded, PageHeader } from '../components/ui'
import { MemberActions } from './actions/MemberActions'

type Row = Schemas['MemberRow']

const VIP_LABELS: Record<string, string> = { pass: 'day pass', one_percent: 'the 1%', henchman: 'henchman' }

export function Members() {
  const members = useGuildQuery<Row[]>('/members')
  return (
    <>
      <PageHeader title="Members" />
      <Card>
        <p className="mb-3 text-sm text-stone-500">Click a member to see only their records on the other pages.</p>
        <Loaded query={members}>
          {(rows) => (
            <DataTable
              rows={rows}
              rowKey={(r) => r.user_id}
              initialSort={{ key: 'balance', desc: true }}
              columns={[
                { key: 'member', header: 'Member', render: (r) => <MemberName id={r.user_id} />, sort: (r) => r.user_id },
                { key: 'balance', header: 'Balance', render: (r) => <Amount value={r.balance} />, sort: (r) => r.balance.value, className: 'text-right' },
                { key: 'shadow', header: 'ShadowBeans', render: (r) => <Amount value={r.shadow_balance} />, sort: (r) => r.shadow_balance.value, className: 'text-right' },
                { key: 'jailed', header: 'Jail', render: (r) => (r.jailed ? <Badge color="red">in jail</Badge> : null), sort: (r) => Number(r.jailed) },
                { key: 'vip', header: 'VIP', render: (r) => r.vip.map((v) => <Badge key={v} color="purple">{VIP_LABELS[v] ?? v}</Badge>), sort: (r) => r.vip.length },
                { key: 'times', header: 'Times jailed', render: (r) => r.times_jailed, sort: (r) => r.times_jailed, className: 'text-right' },
                { key: 'tomatoes', header: 'Tomatoes', render: (r) => r.tomatoes_received, sort: (r) => r.tomatoes_received, className: 'text-right' },
                { key: 'left', header: '', render: (r) => (r.in_guild === false ? <Badge>left the server</Badge> : null) },
                { key: 'actions', header: '', render: (r) => <MemberActions userId={r.user_id} jailed={r.jailed} /> },
              ]}
            />
          )}
        </Loaded>
      </Card>
    </>
  )
}
