import { query } from '../api/client'
import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { Amount } from '../components/Amount'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { MemberName, useMemberFilter } from '../components/Names'
import { Badge, Card, Loaded, PageHeader } from '../components/ui'
import { Time } from '../components/Time'

type Heist = Schemas['HeistOut']
const COLORS = { active: 'amber', succeeded: 'green', snitched: 'red', expired: 'stone' } as const

function Crew({ heist }: { heist: Heist }) {
  return (
    <ul className="space-y-0.5">
      {heist.crew.map((m) => (
        <li key={m.user_id} className="flex flex-wrap items-center gap-1">
          <MemberName id={m.user_id} />
          {m.user_id === heist.leader_user_id && <Badge>leader</Badge>}
          {m.share && (
            <span className="text-xs text-stone-500">
              took <Amount value={m.share} />
            </span>
          )}
          {m.fine && (
            <span className="text-xs text-stone-500">
              fined <Amount value={m.fine} />
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

function HeistTable({ active }: { active: boolean }) {
  const member = useMemberFilter()
  const paging = usePaging(50, [member])
  const data = useGuildQuery<Schemas['Page_HeistOut_']>(`/heists${query({ active, user_id: member, limit: paging.limit, offset: paging.offset })}`)
  return (
    <Loaded query={data}>
      {(page) => (
        <>
          <DataTable
            rows={page.items}
            rowKey={(h) => String(h.id)}
            empty={active ? 'No heists are being plotted. That you know of.' : 'No heists have finished yet.'}
            columns={[
              { key: 'code', header: 'Codeword', render: (h) => <code>{h.code}</code> },
              { key: 'target', header: 'Target', render: (h) => <MemberName id={h.target_user_id} /> },
              { key: 'crew', header: 'Crew', render: (h) => <Crew heist={h} /> },
              { key: 'started', header: 'Started', render: (h) => <Time value={h.started_at} /> },
              active
                ? { key: 'ends', header: 'Falls apart', render: (h) => <Time value={h.ends_at} /> }
                : { key: 'finished', header: 'Finished', render: (h) => <Time value={h.finished_at} /> },
              { key: 'status', header: 'Outcome', render: (h) => <Badge color={COLORS[h.status as keyof typeof COLORS]}>{h.status}</Badge> },
              {
                key: 'money',
                header: 'Money',
                render: (h) =>
                  h.loot ? (
                    <>loot <Amount value={h.loot} /></>
                  ) : h.snitch_user_id ? (
                    <span className="inline-flex flex-wrap items-center gap-1">
                      snitch <MemberName id={h.snitch_user_id} /> {h.snitch_reward && <>got <Amount value={h.snitch_reward} /></>}
                    </span>
                  ) : null,
              },
            ]}
          />
          <Pagination total={page.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
        </>
      )}
    </Loaded>
  )
}

export function Heists() {
  return (
    <>
      <PageHeader title="Heists" />
      <p className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-900">
        This page is secret from members. Crews, codewords and who snitched are never shown in Discord. Don't screenshot it into the server.
      </p>
      <div className="space-y-4">
        <Card title="Active">
          <HeistTable active />
        </Card>
        <Card title="History">
          <HeistTable active={false} />
        </Card>
      </div>
      <ConfigPanel section="heists" />
    </>
  )
}
