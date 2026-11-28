import { useState } from 'react'
import { DataTable } from '../../components/DataTable'
import { Badge, Card, Empty, fieldClass, Loaded, Stat } from '../../components/ui'
import { Time } from '../../components/Time'
import { largeUrl, percent, thumbUrl, useRun, useRuns, type Dataset, type Prediction, type Results as RunResults } from './api'
import { QuickActions } from './QuickActions'

function Confusion({ results, cell, onCell }: { results: RunResults; cell: [number, number] | null; onCell: (cell: [number, number] | null) => void }) {
  const n = results.labels.length
  const max = Math.max(1, ...results.confusion.flat())
  return (
    <div className="overflow-x-auto">
      <div className="inline-grid gap-0.5 text-xs" style={{ gridTemplateColumns: `auto repeat(${n}, minmax(2.5rem, 1fr))` }}>
        <div className="p-1 text-stone-500">true ↓ / guessed →</div>
        {results.labels.map((label) => (
          <div key={label} className="truncate p-1 text-center font-medium" title={label}>
            {label}
          </div>
        ))}
        {results.confusion.map((row, i) => (
          <div key={i} className="contents">
            <div className="truncate p-1 font-medium">{results.labels[i]}</div>
            {row.map((count, j) => {
              const active = cell?.[0] === i && cell?.[1] === j
              const strength = count / max
              return (
                <button
                  key={j}
                  onClick={() => onCell(active ? null : [i, j])}
                  className={`rounded p-1 text-center tabular-nums ${active ? 'ring-2 ring-stone-900' : ''}`}
                  style={{ background: count ? (i === j ? `rgba(22, 163, 74, ${0.15 + 0.7 * strength})` : `rgba(220, 38, 38, ${0.15 + 0.7 * strength})`) : '#f5f5f4' }}
                  title={`${count} ${results.labels[i]} photo${count === 1 ? '' : 's'} guessed as ${results.labels[j]}`}
                >
                  {count}
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

function PredictionTile({ prediction, labels }: { prediction: Prediction; labels: string[] }) {
  const top = Object.entries(prediction.probabilities).sort(([, a], [, b]) => b - a).slice(0, 3)
  return (
    <div className="flex gap-3 rounded-lg border border-stone-200 p-2">
      <img src={thumbUrl(prediction.image_id)} loading="lazy" alt={prediction.label} className="h-28 w-28 shrink-0 rounded object-cover" />
      <div className="min-w-0 space-y-1 text-sm">
        <div>
          Really <strong>{prediction.label}</strong>, guessed <strong className={prediction.correct ? 'text-green-700' : 'text-red-700'}>{prediction.predicted}</strong>
        </div>
        <div className="text-xs text-stone-500">{top.map(([label, p]) => `${label} ${percent(p)}`).join(' · ')}</div>
        <QuickActions ids={[prediction.image_id]} labels={labels} compact />
      </div>
    </div>
  )
}

/**
 * How one label's photos were grouped for cross validation. Each group is a k-means cluster that was held
 * out together, so the photos in a group should look alike. Read from the run's predictions, where a
 * label's photos in one fold are one cluster.
 */
function Clusters({ results }: { results: RunResults }) {
  const scored = results.labels.filter((l) => results.per_label[l].val > 0)
  const [chosen, setChosen] = useState<string | undefined>()
  const label = chosen !== undefined && scored.includes(chosen) ? chosen : scored[0]
  if (label === undefined) return null
  const byFold = new Map<number, Prediction[]>()
  for (const p of results.predictions) {
    if (p.label !== label || p.fold === undefined) continue
    byFold.set(p.fold, [...(byFold.get(p.fold) ?? []), p])
  }
  const clusters = [...byFold.entries()].sort(([, a], [, b]) => b.length - a.length)
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">Clusters held out together</h3>
        <select className={fieldClass} value={label} onChange={(e) => setChosen(e.target.value)}>
          {scored.map((l) => (
            <option key={l} value={l}>
              {l} ({results.per_label[l].val})
            </option>
          ))}
        </select>
        <span className="text-xs text-stone-500">
          Each row was guessed by a model that saw none of it, so a row should hold look-alikes. A red ring is a wrong guess.
          {clusters.every(([, photos]) => photos.length === 1) && ' This label has too few photos to cluster: one photo per fold.'}
        </span>
      </div>
      <div className="space-y-2">
        {clusters.map(([fold, photos]) => {
          const right = photos.filter((p) => p.correct).length
          return (
            <div key={fold} className="flex gap-3 rounded-lg border border-stone-200 p-2">
              <div className="w-24 shrink-0 text-xs text-stone-500">
                <div className="text-sm font-medium text-stone-800">Fold {fold + 1}</div>
                {photos.length} photo{photos.length === 1 ? '' : 's'}
                <div className={right === photos.length ? 'text-green-700' : 'text-red-700'}>{right} right</div>
              </div>
              <div className="flex flex-wrap gap-1">
                {photos.map((p) => (
                  <a key={p.image_id} href={largeUrl(p.image_id)} target="_blank" rel="noreferrer" title={p.correct ? `Guessed ${p.predicted}` : `Wrongly guessed ${p.predicted}`}>
                    <img src={thumbUrl(p.image_id)} loading="lazy" alt={label} className={`h-20 w-20 rounded object-cover ${p.correct ? '' : 'ring-2 ring-red-500'}`} />
                  </a>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** One run's scores: the numbers, a table per label, the confusion matrix and the photos it got wrong. */
export function Results({ dataset }: { dataset: Dataset }) {
  const runs = useRuns(dataset.name)
  const [chosen, setChosen] = useState<number | undefined>()
  const [cell, setCell] = useState<[number, number] | null>(null)
  const runId = chosen ?? dataset.latest_run?.id
  const run = useRun(runId)
  const labels = dataset.labels.map((l) => l.label)
  return (
    <Card
      title="Results"
      actions={
        <Loaded query={runs}>
          {(page) => (
            <select className={fieldClass} value={runId ?? ''} onChange={(e) => { setChosen(Number(e.target.value)); setCell(null) }}>
              {!runId && <option value="">No runs yet</option>}
              {page.items.map((r) => (
                <option key={r.id} value={r.id} disabled={r.status !== 'succeeded'}>
                  #{r.id} {new Date(r.started_at).toLocaleString()} · {r.status}
                  {r.accuracy !== null ? ` · ${percent(r.accuracy)}` : ''}
                  {r.status === 'succeeded' && r.method === 'holdout' ? ' (val set)' : ''}
                </option>
              ))}
            </select>
          )}
        </Loaded>
      }
    >
      {runId === undefined ? (
        <Empty>This dataset hasn't been retrained yet. Press Retrain to train and score a model.</Empty>
      ) : (
        <Loaded query={run}>
          {(r) => {
            const results = r.results as RunResults | null
            if (!results) return <Empty>This run has no results{r.error ? `: ${r.error}` : ''}.</Empty>
            const cross = r.method === 'cross_validation'
            const unscored = results.unscored_labels ?? []
            const unseen = results.unseen_val_labels ?? []
            // The surest mistakes first: a photo the model is certain about and "wrong" on is often mislabeled.
            const sureness = (p: Prediction) => p.probabilities[p.predicted] ?? 0
            const shown = (
              cell
                ? results.predictions.filter((p) => p.label === results.labels[cell[0]] && p.predicted === results.labels[cell[1]])
                : results.predictions.filter((p) => !p.correct)
            ).sort((a, b) => sureness(b) - sureness(a))
            return (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
                  <Stat label="Accuracy">{percent(r.accuracy)}</Stat>
                  <Stat label="Log loss">{r.log_loss?.toFixed(3) ?? '-'}</Stat>
                  <Stat label={cross ? 'Photos trained on' : 'Train photos'}>{r.train_count ?? '-'}</Stat>
                  <Stat label={cross ? 'Photos scored' : 'Val photos scored'}>{r.val_count ?? '-'}</Stat>
                  <Stat label="Finished">
                    <Time value={r.finished_at} />
                  </Stat>
                </div>
                <p className="text-sm text-stone-600">
                  {cross
                    ? `Scored by ${results.folds ?? 10}-fold cross validation: each photo was guessed by a model trained without it and without its look-alikes. The model in use is trained on all of them. `
                    : "This older run scored a fixed val set the model didn't train on. Its scores don't compare with cross validated runs. "}
                  {unscored.length > 0 && `${unscored.join(', ')} ${unscored.length === 1 ? 'has' : 'have'} one photo each, so ${unscored.length === 1 ? 'it trains' : 'they train'} the model but can't be scored. `}
                  {results.skipped.length > 0 && `${results.skipped.length} photo(s) couldn't be read. `}
                  {unseen.length > 0 && `Val photos of ${unseen.join(', ')} weren't scored: those labels have no train photos.`}
                </p>
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                  <DataTable
                    rows={results.labels}
                    rowKey={(l) => l}
                    columns={[
                      { key: 'label', header: 'Label', render: (l) => l },
                      { key: 'train', header: cross ? 'Photos' : 'Train', render: (l) => results.per_label[l].train, sort: (l) => results.per_label[l].train, className: 'text-right' },
                      { key: 'val', header: cross ? 'Scored' : 'Val', render: (l) => results.per_label[l].val, sort: (l) => results.per_label[l].val, className: 'text-right' },
                      { key: 'precision', header: 'Precision', render: (l) => percent(results.per_label[l].precision), sort: (l) => results.per_label[l].precision, className: 'text-right' },
                      { key: 'recall', header: 'Recall', render: (l) => percent(results.per_label[l].recall), sort: (l) => results.per_label[l].recall, className: 'text-right' },
                    ]}
                  />
                  <div>
                    <p className="mb-2 text-xs text-stone-500">Click a cell to see its photos.</p>
                    <Confusion results={results} cell={cell} onCell={setCell} />
                  </div>
                </div>
                <div>
                  <h3 className="mb-2 font-semibold">
                    {cell ? `${results.labels[cell[0]]} guessed as ${results.labels[cell[1]]}` : cross ? 'Photos it got wrong, surest first' : 'Val photos it got wrong'} ({shown.length})
                    {cell && (
                      <button className="ml-2 text-sm font-normal text-amber-800 hover:underline" onClick={() => setCell(null)}>
                        show every mistake
                      </button>
                    )}
                  </h3>
                  {shown.length === 0 ? (
                    <Empty>Nothing here. {cell ? '' : 'A perfect score, or nothing to score.'}</Empty>
                  ) : (
                    <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
                      {shown.map((p) => (
                        <PredictionTile key={p.image_id} prediction={p} labels={labels} />
                      ))}
                    </div>
                  )}
                </div>
                {cross && <Clusters key={r.id} results={results} />}
              </div>
            )
          }}
        </Loaded>
      )}
    </Card>
  )
}

export function RunHistory({ dataset }: { dataset: Dataset }) {
  const runs = useRuns(dataset.name)
  return (
    <Card title="Run history">
      <Loaded query={runs}>
        {(page) => (
          <DataTable
            rows={page.items}
            rowKey={(r) => String(r.id)}
            empty="No retrains yet."
            columns={[
              { key: 'when', header: 'Started', render: (r) => <Time value={r.started_at} /> },
              { key: 'who', header: 'Started by', render: (r) => (r.triggered_by === 'auto' ? <Badge>auto retrain</Badge> : r.triggered_by) },
              { key: 'status', header: 'Status', render: (r) => <Badge color={r.status === 'succeeded' ? 'green' : r.status === 'failed' ? 'red' : 'amber'}>{r.status}</Badge> },
              { key: 'train', header: 'Trained on', render: (r) => r.train_count ?? '', className: 'text-right' },
              { key: 'val', header: 'Scored', render: (r) => r.val_count ?? '', className: 'text-right' },
              { key: 'accuracy', header: 'Accuracy', render: (r) => percent(r.accuracy), className: 'text-right' },
              {
                key: 'method',
                header: 'Scored by',
                render: (r) => (r.status !== 'succeeded' ? null : r.method === 'cross_validation' ? <Badge color="green">cross validation</Badge> : <Badge title="A fixed val set. Doesn't compare with cross validated runs.">val set</Badge>),
              },
              { key: 'error', header: 'Error', render: (r) => <span className="text-xs text-red-700">{r.error ?? ''}</span> },
            ]}
          />
        )}
      </Loaded>
    </Card>
  )
}
