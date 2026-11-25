import { useState } from 'react'
import { query } from '../api/client'
import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { Amount } from '../components/Amount'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { LedgerTable } from '../components/Ledger'
import { MemberName, useMemberFilter } from '../components/Names'
import { Card, inputClass, Loaded, PageHeader, Stat } from '../components/ui'
import { CoinActions } from './actions/CoinActions'

export function Coins() {
  const member = useMemberFilter()
  const [kind, setKind] = useState('')
  const paging = usePaging(50, [member, kind])
  const accounts = useGuildQuery<Schemas['Accounts']>('/coins/accounts')
  const ledger = useGuildQuery<Schemas['LedgerPage']>(`/coins/ledger${query({ user_id: member, kind, limit: paging.limit, offset: paging.offset })}`)
  return (
    <>
      <PageHeader title="Coins">
        <CoinActions />
      </PageHeader>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card title="Accounts">
          <Loaded query={accounts}>
            {(data) => (
              <>
                {data.house && (
                  <div className="mb-4 flex gap-6 rounded-lg bg-stone-100 p-3">
                    <Stat label="The house">
                      <Amount value={data.house.balance} />
                    </Stat>
                    <p className="self-center text-xs text-stone-500">Takes bribes, purchases and market leftovers. The only account allowed to go negative.</p>
                  </div>
                )}
                <DataTable
                  rows={data.accounts}
                  rowKey={(r) => r.user_id}
                  initialSort={{ key: 'balance', desc: true }}
                  columns={[
                    { key: 'member', header: 'Member', render: (r) => <MemberName id={r.user_id} /> },
                    { key: 'balance', header: 'Balance', render: (r) => <Amount value={r.balance} />, sort: (r) => r.balance.value, className: 'text-right' },
                    { key: 'shadow', header: 'Shadow', render: (r) => <Amount value={r.shadow_balance} />, sort: (r) => r.shadow_balance.value, className: 'text-right' },
                    { key: 'actions', header: '', render: (r) => <CoinActions userId={r.user_id} compact /> },
                  ]}
                />
              </>
            )}
          </Loaded>
        </Card>
        <Card
          title="Ledger"
          actions={
            <Loaded query={ledger}>
              {(data) => (
                <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value)}>
                  <option value="">Every kind</option>
                  {data.kinds.map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              )}
            </Loaded>
          }
        >
          <Loaded query={ledger}>
            {(data) => (
              <>
                <LedgerTable rows={data.items} />
                <Pagination total={data.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
              </>
            )}
          </Loaded>
        </Card>
      </div>
      <ConfigPanel section="coins" />
    </>
  )
}
