import { query } from '../api/client'
import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { DiscordLink } from '../components/Names'
import { Card, Loaded, PageHeader } from '../components/ui'
import { DateText } from '../components/Time'

export function DailyPosts() {
  const births = usePaging(50)
  const holidays = usePaging(50)
  const data = useGuildQuery<Schemas['DailyPostsOut']>(`/daily-posts${query({ limit: 50, birthdays_offset: births.offset, holidays_offset: holidays.offset })}`)
  return (
    <>
      <PageHeader title="Daily posts" />
      <Loaded query={data}>
        {(d) => (
          <div className="space-y-4">
            <Card title="Birthday messages">
              <DataTable
                rows={d.birthdays.items}
                rowKey={(b) => String(b.id)}
                empty="No birthdays celebrated yet."
                columns={[
                  { key: 'date', header: 'Sent', render: (b) => <DateText value={b.sent_on} /> },
                  { key: 'person', header: 'Person', render: (b) => b.person_name },
                  { key: 'holiday', header: 'Holiday used', render: (b) => b.used_holiday ?? '' },
                  { key: 'event', header: 'On this day used', render: (b) => <span className="line-clamp-2">{b.used_on_this_day ?? ''}</span> },
                  { key: 'link', header: '', render: (b) => <DiscordLink channelId={b.channel_id} messageId={b.message_id}>message</DiscordLink> },
                ]}
              />
              <Pagination total={d.birthdays.total} limit={births.limit} offset={births.offset} onChange={births.setOffset} />
            </Card>
            <Card title="Action holidays">
              <DataTable
                rows={d.action_holidays.items}
                rowKey={(h) => String(h.id)}
                empty="No action holidays posted yet."
                columns={[
                  { key: 'date', header: 'Sent', render: (h) => <DateText value={h.sent_on} /> },
                  { key: 'name', header: 'Holiday', render: (h) => <a href={h.holiday_link} target="_blank" rel="noreferrer">{h.holiday_name}</a> },
                ]}
              />
              <Pagination total={d.action_holidays.total} limit={holidays.limit} offset={holidays.offset} onChange={holidays.setOffset} />
            </Card>
          </div>
        )}
      </Loaded>
      <ConfigPanel section="birthday" />
      <ConfigPanel section="action_holiday" />
    </>
  )
}
