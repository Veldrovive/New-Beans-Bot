import { useState } from 'react'
import { query } from '../api/client'
import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { DiscordLink, MemberName, useMemberFilter } from '../components/Names'
import { Badge, Card, inputClass, Loaded, PageHeader } from '../components/ui'
import { Time } from '../components/Time'

const COLORS = { pending: 'amber', verified: 'green', rejected: 'red', expired: 'stone' } as const

export function Snipes() {
  const member = useMemberFilter()
  const [status, setStatus] = useState('')
  const paging = usePaging(50, [member, status])
  const data = useGuildQuery<Schemas['SnipesOut']>(`/snipes${query({ status, user_id: member, limit: paging.limit, offset: paging.offset })}`)
  return (
    <>
      <PageHeader title="Snipes">
        <select className={inputClass} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Every status</option>
          {Object.keys(COLORS).map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </PageHeader>
      <Loaded query={data}>
        {(d) => (
          <div className="space-y-4">
            <Card title={`The Council of Teds (${d.majority} ${d.majority === 1 ? "vote decides" : "votes decide"} a snipe)`}>
              <ul className="flex flex-wrap gap-4 text-sm">
                {d.council.map((c) => (
                  <li key={c.key} className="flex items-center gap-2">
                    <MemberName id={c.user_id} />
                    <span className="text-stone-500">
                      {c.council_name}, crest <code>{c.crest_emoji}</code>
                    </span>
                  </li>
                ))}
                {d.nay_emoji && (
                  <li className="text-stone-500">
                    Nay: <code>{d.nay_emoji}</code>
                  </li>
                )}
              </ul>
            </Card>
            <Card title="Snipes">
              <p className="mb-3 text-xs text-stone-500">Read-only. The council decides snipes with reactions in Discord.</p>
              <DataTable
                rows={d.items}
                rowKey={(s) => s.message_id}
                columns={[
                  { key: 'posted', header: 'Posted', render: (s) => <Time value={s.created_at} /> },
                  { key: 'sniper', header: 'Sniper', render: (s) => <MemberName id={s.sniper_user_id} /> },
                  { key: 'target', header: 'Target', render: (s) => <MemberName id={s.target_user_id} /> },
                  { key: 'status', header: 'Status', render: (s) => <Badge color={COLORS[s.status as keyof typeof COLORS]}>{s.status}</Badge> },
                  { key: 'decided', header: 'Decided', render: (s) => <Time value={s.decided_at} /> },
                  { key: 'for', header: 'For', render: (s) => <div className="flex flex-wrap gap-1">{s.votes_for.map((id) => <MemberName key={id} id={id} />)}</div> },
                  { key: 'against', header: 'Against', render: (s) => <div className="flex flex-wrap gap-1">{s.votes_against.map((id) => <MemberName key={id} id={id} />)}</div> },
                  { key: 'photo', header: '', render: (s) => <DiscordLink channelId={s.channel_id} messageId={s.message_id}>photo</DiscordLink> },
                ]}
              />
              <Pagination total={d.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
            </Card>
          </div>
        )}
      </Loaded>
      <ConfigPanel section="snipes" />
      <ConfigPanel section="council_of_teds" />
    </>
  )
}
