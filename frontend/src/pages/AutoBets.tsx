import { query } from '../api/client'
import { useGuildId, useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { Amount } from '../components/Amount'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { MemberName } from '../components/Names'
import { Badge, Card, ErrorBox, Loaded, PageHeader, Stat } from '../components/ui'
import { Time } from '../components/Time'
import { Link } from 'react-router-dom'
import { PostAutoBetButton } from './actions/AutoBetActions'

export function AutoBets() {
  const guildId = useGuildId()
  const paging = usePaging(50)
  const data = useGuildQuery<Schemas['AutoBetsOut']>(`/auto-bets${query({ limit: paging.limit, offset: paging.offset })}`)
  return (
    <>
      <PageHeader title="Automatic bets">
        <PostAutoBetButton />
      </PageHeader>
      <Loaded query={data}>
        {(d) => (
          <div className="space-y-4">
            <Card>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <Stat label="Posts at">{d.post_time ?? '-'}</Stat>
                <Stat label="Chance per day">{d.post_probability === null ? '-' : `${Math.round(d.post_probability * 100)}%`}</Stat>
                <Stat label="Round">{d.round}</Stat>
                <Stat label="Bets file">
                  <span className="font-mono text-sm">{d.bets_file}</span>
                </Stat>
              </div>
            </Card>
            {d.error && (
              <Card title="The bets file is broken">
                <ErrorBox error={d.error} />
              </Card>
            )}
            <Card title={`Bets (${d.bets.filter((b) => b.used_this_round).length} of ${d.bets.length} used this round)`}>
              <DataTable
                rows={d.bets}
                rowKey={(b) => b.bet}
                columns={[
                  { key: 'bet', header: 'Template', render: (b) => <span className={b.used_this_round ? 'text-stone-400' : ''}>{b.bet}</span> },
                  {
                    key: 'params',
                    header: 'Values',
                    render: (b) => (
                      <div className="space-y-0.5 text-xs">
                        {Object.entries(b.params).map(([name, values]) => (
                          <div key={name}>
                            <strong>{name}</strong>: {values.join(', ')}
                          </div>
                        ))}
                      </div>
                    ),
                  },
                  { key: 'choices', header: 'Choices', render: (b) => b.choices.join(' / ') },
                  { key: 'bot', header: 'House bets', render: (b) => (b.bot_bet ? <><Amount value={b.bot_bet[0]} /> to <Amount value={b.bot_bet[1]} /></> : 'usual') },
                  { key: 'admins', header: 'Managers', render: (b) => <div className="flex flex-wrap gap-1">{b.admins.map((id) => <MemberName key={id} id={id} />)}</div> },
                  { key: 'used', header: '', render: (b) => (b.used_this_round ? <Badge>used this round</Badge> : <Badge color="green">fresh</Badge>) },
                ]}
              />
            </Card>
            <Card title="Posted">
              <DataTable
                rows={d.history.items}
                rowKey={(p) => String(p.id)}
                empty="The bot hasn't posted a bet yet."
                columns={[
                  { key: 'posted', header: 'Posted', render: (p) => <Time value={p.posted_at} /> },
                  { key: 'text', header: 'Bet', render: (p) => <Link to={`/g/${guildId}/markets/${p.thread_id}`}>{p.text}</Link> },
                  { key: 'round', header: 'Round', render: (p) => p.round, className: 'text-right' },
                ]}
              />
              <Pagination total={d.history.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
            </Card>
          </div>
        )}
      </Loaded>
      <ConfigPanel section="auto_bets" />
    </>
  )
}
