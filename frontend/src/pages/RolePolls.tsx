import { query } from '../api/client'
import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { DiscordLink, MemberName } from '../components/Names'
import { Badge, Card, Loaded, PageHeader } from '../components/ui'
import { Time } from '../components/Time'

export function RolePolls() {
  const paging = usePaging(50)
  const data = useGuildQuery<Schemas['Page_PollOut_']>(`/role-polls${query({ limit: paging.limit, offset: paging.offset })}`)
  return (
    <>
      <PageHeader title="Role polls" />
      <Card>
        <Loaded query={data}>
          {(page) => (
            <>
              <DataTable
                rows={page.items}
                rowKey={(p) => p.poll_message_id}
                empty="Nobody has tried to rename the robotics students yet."
                columns={[
                  { key: 'adjective', header: 'Adjective', render: (p) => <strong>{p.adjective} Robotics Student</strong> },
                  { key: 'by', header: 'Started by', render: (p) => <MemberName id={p.initiator_user_id} /> },
                  { key: 'when', header: 'When', render: (p) => <Time value={p.created_at} /> },
                  { key: 'active', header: '', render: (p) => (p.active ? <Badge color="green">active</Badge> : <Badge>closed</Badge>) },
                  { key: 'link', header: '', render: (p) => <DiscordLink channelId={p.channel_id} messageId={p.poll_message_id}>poll</DiscordLink> },
                ]}
              />
              <Pagination total={page.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
            </>
          )}
        </Loaded>
      </Card>
      <ConfigPanel section="role_name" />
    </>
  )
}
