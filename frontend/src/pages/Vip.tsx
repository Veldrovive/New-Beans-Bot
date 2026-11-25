import { query } from '../api/client'
import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { Amount } from '../components/Amount'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { MemberName, useMemberFilter } from '../components/Names'
import { Card, Empty, Loaded, PageHeader, Stat } from '../components/ui'
import { Time } from '../components/Time'
import { EndPassButton, RevokeOnePercentButton } from './actions/VipActions'

export function Vip() {
  const member = useMemberFilter()
  const paging = usePaging(50, [member])
  const data = useGuildQuery<Schemas['VipOut']>(`/vip${query({ user_id: member, limit: paging.limit, offset: paging.offset })}`)
  return (
    <>
      <PageHeader title="VIP" />
      <Loaded query={data}>
        {(d) => (
          <div className="space-y-4">
            <Card title="The 1%" actions={d.one_percent && <RevokeOnePercentButton holderId={d.one_percent.holder_user_id} />}>
              {d.one_percent ? (
                <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
                  <Stat label="Holder">
                    <MemberName id={d.one_percent.holder_user_id} />
                  </Stat>
                  <Stat label="Paid">
                    <Amount value={d.one_percent.paid} />
                  </Stat>
                  <Stat label="Next price">
                    <Amount value={d.next_price} />
                  </Stat>
                  <Stat label="Since">
                    <Time value={d.one_percent.claimed_at} />
                  </Stat>
                  <Stat label="Henchman">{d.one_percent.henchman_user_id ? <MemberName id={d.one_percent.henchman_user_id} /> : 'none'}</Stat>
                </div>
              ) : (
                <Empty>
                  Nobody holds the title. It costs <Amount value={d.next_price} /> to claim.
                </Empty>
              )}
            </Card>
            <Card title="Active day passes">
              <DataTable
                rows={d.passes}
                rowKey={(p) => String(p.id)}
                empty="Nobody is slumming it in the VIP channel right now."
                columns={[
                  { key: 'member', header: 'Member', render: (p) => <MemberName id={p.user_id} /> },
                  { key: 'started', header: 'Bought', render: (p) => <Time value={p.started_at} /> },
                  { key: 'ends', header: 'Expires', render: (p) => <Time value={p.ends_at} /> },
                  { key: 'actions', header: '', render: (p) => <EndPassButton pass={p} /> },
                ]}
              />
            </Card>
            <Card title="Pass history">
              <DataTable
                rows={d.history.items}
                rowKey={(p) => String(p.id)}
                columns={[
                  { key: 'member', header: 'Member', render: (p) => <MemberName id={p.user_id} /> },
                  { key: 'started', header: 'Bought', render: (p) => <Time value={p.started_at} /> },
                  { key: 'ends', header: 'Paid until', render: (p) => <Time value={p.ends_at} /> },
                  { key: 'ended', header: 'Ended', render: (p) => <Time value={p.ended_at} empty="active" /> },
                ]}
              />
              <Pagination total={d.history.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
            </Card>
          </div>
        )}
      </Loaded>
      <ConfigPanel section="vip" />
    </>
  )
}
