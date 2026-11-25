import { useState } from 'react'
import { query } from '../api/client'
import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { ConfigPanel } from '../components/ConfigPanel'
import { DataTable, Pagination, usePaging } from '../components/DataTable'
import { DiscordLink, MemberName } from '../components/Names'
import { Badge, Card, Empty, Loaded, PageHeader } from '../components/ui'
import { Time } from '../components/Time'
import { useDatasets } from './classifier/api'
import { DatasetHeader } from './classifier/Header'
import { Photos } from './classifier/Photos'
import { Results, RunHistory } from './classifier/Results'

/** The bot's recent guesses in this server, and what members said about them. */
export function RecentGuesses() {
  const paging = usePaging(50)
  const data = useGuildQuery<Schemas['Page_GuessOut_']>(`/classifier/guesses${query({ limit: paging.limit, offset: paging.offset })}`)
  return (
    <Card title="Recent guesses in this server">
      <Loaded query={data}>
        {(page) => (
          <>
            <DataTable
              rows={page.items}
              rowKey={(g) => `${g.message_id}-${g.classifier_name}`}
              empty="The bot hasn't guessed at any photos here yet."
              columns={[
                { key: 'when', header: 'When', render: (g) => <Time value={g.processed_at} /> },
                { key: 'model', header: 'Model', render: (g) => g.classifier_name },
                { key: 'guess', header: 'Guessed', render: (g) => g.predicted_label ?? '?' },
                {
                  key: 'verdict',
                  header: 'Members said',
                  render: (g) =>
                    g.corrected_label === null ? (
                      <span className="text-stone-400">nothing</span>
                    ) : g.corrected_label === g.predicted_label ? (
                      <Badge color="green">confirmed</Badge>
                    ) : (
                      <Badge color="red">{g.corrected_label}</Badge>
                    ),
                },
                { key: 'who', header: 'Who', render: (g) => (g.corrected_by ? <MemberName id={g.corrected_by} /> : null) },
                {
                  key: 'links',
                  header: '',
                  render: (g) => (
                    <span className="flex gap-2">
                      <DiscordLink channelId={g.channel_id} messageId={g.message_id}>photo</DiscordLink>
                      {g.reply_message_id && <DiscordLink channelId={g.channel_id} messageId={g.reply_message_id}>reply</DiscordLink>}
                    </span>
                  ),
                },
              ]}
            />
            <Pagination total={page.total} limit={paging.limit} offset={paging.offset} onChange={paging.setOffset} />
          </>
        )}
      </Loaded>
    </Card>
  )
}

function Datasets() {
  const datasets = useDatasets()
  const [chosen, setChosen] = useState<string | undefined>()
  return (
    <Loaded query={datasets}>
      {(list) => {
        if (list.length === 0) return <Empty>No server has a classifier in its config, and no photos have been imported.</Empty>
        const dataset = list.find((d) => d.name === chosen) ?? list[0]
        return (
          <div className="space-y-4">
            <div className="flex gap-1 border-b border-stone-200">
              {list.map((d) => (
                <button
                  key={d.name}
                  onClick={() => setChosen(d.name)}
                  className={`-mb-px border-b-2 px-3 py-1.5 text-sm font-medium ${d.name === dataset.name ? 'border-amber-600 text-stone-900' : 'border-transparent text-stone-500 hover:text-stone-800'}`}
                >
                  {d.name}
                  {d.running && ' (retraining)'}
                </button>
              ))}
            </div>
            {/* Keyed by dataset, so switching tabs starts each section fresh. */}
            <DatasetHeader key={`h-${dataset.name}`} dataset={dataset} />
            <Photos key={`p-${dataset.name}`} dataset={dataset} />
            <Results key={`r-${dataset.name}`} dataset={dataset} />
            <RunHistory key={`k-${dataset.name}`} dataset={dataset} />
          </div>
        )
      }}
    </Loaded>
  )
}

export function Classifier() {
  return (
    <>
      <PageHeader title="Classifier" />
      <p className="mb-4 text-sm text-stone-500">Datasets are shared by every server that uses the same model. Recent guesses are for this server.</p>
      <Datasets />
      <div className="mt-4">
        <RecentGuesses />
      </div>
      <ConfigPanel section="classifier" />
    </>
  )
}
