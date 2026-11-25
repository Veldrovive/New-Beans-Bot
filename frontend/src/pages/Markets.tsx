import { useState } from 'react'
import { Link, Route, Routes, useParams } from 'react-router-dom'
import { query } from '../api/client'
import { useGuildId, useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { Amount } from '../components/Amount'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { LedgerTable } from '../components/Ledger'
import { ChannelName, DiscordLink, MemberName, useMemberFilter } from '../components/Names'
import { Badge, Card, inputClass, Loaded, PageHeader, Stat } from '../components/ui'
import { Time } from '../components/Time'
import { MarketActions } from './actions/MarketActions'

const STATUS_COLOR = { open: 'green', closed: 'amber', resolved: 'blue', canceled: 'stone', nullified: 'red' } as const

export function StatusBadge({ status }: { status: string }) {
  return <Badge color={STATUS_COLOR[status as keyof typeof STATUS_COLOR] ?? 'stone'}>{status}</Badge>
}

function MarketList() {
  const guildId = useGuildId()
  const member = useMemberFilter()
  const [status, setStatus] = useState('')
  const paging = usePaging(50, [member, status])
  const markets = useGuildQuery<Schemas['Page_MarketRowOut_']>(`/markets${query({ status, user_id: member, limit: paging.limit, offset: paging.offset })}`)
  return (
    <>
      <PageHeader title="Markets">
        <select className={inputClass} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Every status</option>
          {Object.keys(STATUS_COLOR).map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </PageHeader>
      <Card>
        <Loaded query={markets}>
          {(page) => (
            <>
              <DataTable
                rows={page.items}
                rowKey={(r) => r.thread_id}
                columns={[
                  { key: 'thread', header: 'Thread', render: (r) => <Link to={`/g/${guildId}/markets/${r.thread_id}`}><ChannelName id={r.thread_id} /></Link> },
                  { key: 'creator', header: 'Creator', render: (r) => <MemberName id={r.creator_id} /> },
                  { key: 'managers', header: 'Managers', render: (r) => <div className="flex flex-wrap gap-1">{r.admin_ids.map((id) => <MemberName key={id} id={id} />)}</div> },
                  { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status} />, sort: (r) => r.status },
                  { key: 'pool', header: 'Pool', render: (r) => <Amount value={r.pool} />, sort: (r) => r.pool.value, className: 'text-right' },
                  { key: 'bets', header: 'Bets', render: (r) => r.bets, className: 'text-right' },
                  { key: 'created', header: 'Created', render: (r) => <Time value={r.created_at} /> },
                  { key: 'resolved', header: 'Resolved', render: (r) => <Time value={r.resolved_at} /> },
                  { key: 'winner', header: 'Winner', render: (r) => r.winning_choice ?? '' },
                ]}
              />
              <Pagination total={page.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
            </>
          )}
        </Loaded>
      </Card>
      <ConfigPanel section="gambling" />
    </>
  )
}

function MarketDetail() {
  const { threadId = '' } = useParams()
  const guildId = useGuildId()
  const detail = useGuildQuery<Schemas['MarketDetailOut']>(`/markets/${threadId}`)
  return (
    <Loaded query={detail}>
      {(d) => (
        <>
          <PageHeader title="Market">
            <Link to={`/g/${guildId}/markets`}>All markets</Link>
          </PageHeader>
          <div className="space-y-4">
            <Card
              title={
                <span className="flex items-center gap-2">
                  <ChannelName id={d.market.thread_id} /> <StatusBadge status={d.market.status} />
                </span>
              }
              actions={
                <>
                  <DiscordLink channelId={d.market.thread_id}>Open the thread</DiscordLink>
                  <MarketActions market={d.market} />
                </>
              }
            >
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <Stat label="Pool">
                  <Amount value={d.market.pool} />
                </Stat>
                <Stat label="Created">
                  <Time value={d.market.created_at} />
                </Stat>
                <Stat label="Resolved">
                  <Time value={d.market.resolved_at} />
                </Stat>
                <Stat label="Winner">{d.market.winning_choice ?? '-'}</Stat>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
                Creator <MemberName id={d.market.creator_id} /> {d.market.admin_ids.length > 0 && <>Managers {d.market.admin_ids.map((id) => <MemberName key={id} id={id} />)}</>}
              </div>
              {d.auto_bet && (
                <p className="mt-3 rounded-md bg-stone-100 p-2 text-sm">
                  Automatic bet from round {d.auto_bet.round}: <em>{d.auto_bet.bet}</em>, posted <Time value={d.auto_bet.posted_at} />.
                </p>
              )}
            </Card>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
              <Card title="Totals per choice">
                <DataTable
                  rows={d.totals}
                  rowKey={(r) => r.choice}
                  columns={[
                    { key: 'choice', header: 'Choice', render: (r) => <span className={r.choice === d.market.winning_choice ? 'font-bold' : ''}>{r.choice}</span> },
                    { key: 'amount', header: 'Staked', render: (r) => <Amount value={r.amount} />, className: 'text-right' },
                  ]}
                />
              </Card>
              <Card title="Bets">
                <DataTable
                  rows={d.bets}
                  rowKey={(r) => r.user_id}
                  empty="No bets yet."
                  columns={[
                    { key: 'member', header: 'Bettor', render: (r) => <MemberName id={r.user_id} /> },
                    { key: 'secret', header: '', render: (r) => (r.secret ? <Badge color="purple" title="Members can't see this bet until the market resolves">secret house bet</Badge> : null) },
                    { key: 'choice', header: 'Choice', render: (r) => r.choice },
                    { key: 'amount', header: 'Amount', render: (r) => <Amount value={r.amount} />, sort: (r) => r.amount.value, className: 'text-right' },
                    { key: 'placed', header: 'Placed', render: (r) => <Time value={r.placed_at} /> },
                  ]}
                />
              </Card>
            </div>
            <Card title="Ledger for this market">
              <LedgerTable rows={d.ledger} />
            </Card>
          </div>
        </>
      )}
    </Loaded>
  )
}

export function Markets() {
  return (
    <Routes>
      <Route index element={<MarketList />} />
      <Route path=":threadId" element={<MarketDetail />} />
    </Routes>
  )
}
