import { query } from '../api/client'
import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { DiscordLink, MemberName, useMemberFilter } from '../components/Names'
import { Badge, Card, Loaded, PageHeader } from '../components/ui'
import { Time } from '../components/Time'
import { JailActions, ReleaseButton } from './actions/JailActions'

type Sentence = Schemas['SentenceRow']

function Offending({ s }: { s: Sentence }) {
  return s.offending_message_id ? (
    <DiscordLink channelId={s.channel_id} messageId={s.offending_message_id}>
      message
    </DiscordLink>
  ) : null
}

export function Jail() {
  const member = useMemberFilter()
  const paging = usePaging(50, [member])
  const current = useGuildQuery<Sentence[]>('/jail/current')
  const history = useGuildQuery<Schemas['Page_SentenceRow_']>(`/jail/sentences${query({ user_id: member, limit: paging.limit, offset: paging.offset })}`)
  const stats = useGuildQuery<Schemas['JailStatsOut']>('/jail/stats')
  return (
    <>
      <PageHeader title="Jail">
        <JailActions />
      </PageHeader>
      <div className="space-y-4">
        <Card title="In jail now">
          <Loaded query={current}>
            {(rows) => (
              <DataTable
                rows={rows}
                rowKey={(r) => String(r.id)}
                empty="The stocks are empty. Suspicious."
                columns={[
                  { key: 'member', header: 'Prisoner', render: (r) => <MemberName id={r.user_id} /> },
                  { key: 'reason', header: 'Reason', render: (r) => <Badge>{r.reason ?? 'unknown'}</Badge> },
                  { key: 'started', header: 'Started', render: (r) => <Time value={r.started_at} /> },
                  { key: 'ends', header: 'Ends', render: (r) => <Time value={r.ends_at} /> },
                  { key: 'humiliated', header: 'Humiliated', render: (r) => (r.humiliated ? 'yes' : 'not yet') },
                  { key: 'msg', header: '', render: (r) => <Offending s={r} /> },
                  { key: 'actions', header: '', render: (r) => <ReleaseButton userId={r.user_id} /> },
                ]}
              />
            )}
          </Loaded>
        </Card>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card title="Most jailed">
            <Loaded query={stats}>
              {(s) => (
                <DataTable
                  rows={s.most_jailed}
                  rowKey={(r) => r.user_id}
                  columns={[
                    { key: 'member', header: 'Member', render: (r) => <MemberName id={r.user_id} /> },
                    { key: 'count', header: 'Times', render: (r) => r.count, className: 'text-right' },
                  ]}
                />
              )}
            </Loaded>
          </Card>
          <Card title="Tomatoes">
            <Loaded query={stats}>
              {(s) => (
                <DataTable
                  rows={s.tomatoes}
                  rowKey={(r) => r.user_id}
                  initialSort={{ key: 'received', desc: true }}
                  columns={[
                    { key: 'member', header: 'Member', render: (r) => <MemberName id={r.user_id} /> },
                    { key: 'received', header: 'Received', render: (r) => r.received, sort: (r) => r.received, className: 'text-right' },
                    { key: 'thrown', header: 'Thrown', render: (r) => r.thrown, sort: (r) => r.thrown, className: 'text-right' },
                    { key: 'innocent', header: 'At innocents', render: (r) => r.at_innocents, sort: (r) => r.at_innocents, className: 'text-right' },
                  ]}
                />
              )}
            </Loaded>
          </Card>
        </div>
        <Card title="Pending mega jail renames">
          <Loaded query={stats}>
            {(s) => (
              <DataTable
                rows={s.pending_renames}
                rowKey={(r) => String(r.id)}
                empty="Nobody is waiting for a new name."
                columns={[
                  { key: 'member', header: 'Target', render: (r) => <MemberName id={r.target_user_id} /> },
                  { key: 'prompt', header: 'Prompt message', render: (r) => <span className="font-mono text-xs">{r.prompt_message_id}</span> },
                ]}
              />
            )}
          </Loaded>
        </Card>
        <Card title="Sentence history">
          <Loaded query={history}>
            {(page) => (
              <>
                <DataTable
                  rows={page.items}
                  rowKey={(r) => String(r.id)}
                  columns={[
                    { key: 'member', header: 'Member', render: (r) => <MemberName id={r.user_id} /> },
                    { key: 'reason', header: 'Reason', render: (r) => <Badge>{r.reason ?? 'unknown'}</Badge> },
                    { key: 'started', header: 'Started', render: (r) => <Time value={r.started_at} /> },
                    { key: 'ends', header: 'Ends', render: (r) => <Time value={r.ends_at} /> },
                    { key: 'released', header: 'Released', render: (r) => <Time value={r.released_at} empty="still in" /> },
                    { key: 'why', header: 'Why', render: (r) => r.release_reason ?? '' },
                    { key: 'msg', header: '', render: (r) => <Offending s={r} /> },
                  ]}
                />
                <Pagination total={page.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
              </>
            )}
          </Loaded>
        </Card>
      </div>
      <ConfigPanel section="jail" />
    </>
  )
}
