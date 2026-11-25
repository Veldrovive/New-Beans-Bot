import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable } from '../components/DataTable'
import { MemberName } from '../components/Names'
import { Card, Loaded, PageHeader } from '../components/ui'
import { Time } from '../components/Time'

export function FatCow() {
  const data = useGuildQuery<Schemas['FatCowOut']>('/fat-cow')
  return (
    <>
      <PageHeader title="Fat cow" />
      <Loaded query={data}>
        {(d) => (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <Card title="Eaters">
              <DataTable
                rows={d.members}
                rowKey={(m) => m.user_id}
                initialSort={{ key: 'total', desc: true }}
                columns={[
                  { key: 'member', header: 'Member', render: (m) => <MemberName id={m.user_id} /> },
                  { key: 'visits', header: 'Visits', render: (m) => m.visits, sort: (m) => m.visits, className: 'text-right' },
                  { key: 'total', header: 'Total meters', render: (m) => m.total_meters.toFixed(2), sort: (m) => m.total_meters, className: 'text-right' },
                  { key: 'average', header: 'Average', render: (m) => m.average_meters.toFixed(2), sort: (m) => m.average_meters, className: 'text-right' },
                ]}
              />
            </Card>
            <Card title={`Visits (${d.visits.length})`}>
              <ul className="space-y-1 text-sm">
                {d.visits.map((v) => (
                  <li key={v.id}>
                    <Time value={v.visited_at} />
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        )}
      </Loaded>
      <ConfigPanel section="fat_cow" />
    </>
  )
}
