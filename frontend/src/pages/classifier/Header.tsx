import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api } from '../../api/client'
import type { Schemas } from '../../api/types'
import { ActionButton } from '../../components/ConfirmDialog'
import { useToast } from '../../components/Toasts'
import { Badge, Card, Stat } from '../../components/ui'
import { Time } from '../../components/Time'
import { CLASSIFIER, percent, scoreText, type Dataset, type RunSummary } from './api'

/** Which model is in use, the latest scores, and the Retrain button with its progress bar. */
export function DatasetHeader({ dataset }: { dataset: Dataset }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const progress = useQuery({
    queryKey: [...CLASSIFIER, 'progress', dataset.name],
    queryFn: () => api<Schemas['Progress']>('GET', `/api/classifier/datasets/${dataset.name}/progress`),
    refetchInterval: (q) => (q.state.data?.running || dataset.running ? 2000 : false),
  })
  const running = progress.data?.running ?? dataset.running
  const wasRunning = useRef(running)

  // When a run ends, refetch everything and say how it went.
  useEffect(() => {
    if (wasRunning.current && !running) {
      api<Schemas['Page_RunSummary_']>('GET', `/api/classifier/datasets/${dataset.name}/runs?limit=1`).then((page) => {
        const run: RunSummary | undefined = page.items[0]
        if (run?.status === 'succeeded') toast('ok', `Retrained ${dataset.name}: ${scoreText(run)}.`)
        else if (run) toast('error', `Retraining ${dataset.name} failed: ${run.error ?? 'unknown error'}`)
      })
      queryClient.invalidateQueries({ queryKey: CLASSIFIER })
    }
    wasRunning.current = running
  }, [running, dataset.name, queryClient, toast])

  const run = dataset.latest_run
  const done = progress.data?.done ?? 0
  const total = progress.data?.total ?? 0
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          {dataset.name}
          {dataset.model ? (
            dataset.model.loaded ? (
              <Badge color={dataset.model.source === 'retrained' ? 'green' : 'blue'}>{dataset.model.source === 'retrained' ? 'retrained model' : 'shipped model'}</Badge>
            ) : (
              <Badge color="red">no model loaded</Badge>
            )
          ) : (
            <Badge>classifier not running</Badge>
          )}
        </span>
      }
      actions={
        <ActionButton
          label={running ? 'Retraining...' : 'Retrain'}
          variant="primary"
          title={`Retrain ${dataset.name}`}
          description={
            <p>
              The bot scores the dataset by cross validation (every photo is guessed by a model that didn't train on it), then trains a new model
              on every photo that isn't removed and starts using it. It runs in the background; the old model keeps guessing until it's done.
              Nothing is posted in Discord.
            </p>
          }
          confirmLabel="Retrain"
          run={() => api<Schemas['Started']>('POST', `/api/classifier/datasets/${dataset.name}/retrain`)}
          invalidate={[CLASSIFIER]}
          success={(r) => r.message}
        />
      }
    >
      {running && (
        <div className="mb-3">
          <div className="mb-1 text-sm text-stone-600">
            Embedding photos: {done} of {total || '?'}
          </div>
          <div className="h-2 overflow-hidden rounded bg-stone-200">
            <div className="h-full bg-amber-600 transition-all" style={{ width: `${total ? (100 * done) / total : 5}%` }} />
          </div>
        </div>
      )}
      {dataset.changed_since_latest_run ? (
        <p className="mb-3 rounded-md bg-amber-50 p-2 text-sm text-amber-900">
          {dataset.changed_since_latest_run} photo{dataset.changed_since_latest_run === 1 ? '' : 's'} changed since this model was trained.
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label={run?.method === 'holdout' ? 'Val accuracy' : 'Accuracy'}>{run ? percent(run.accuracy) : 'never retrained'}</Stat>
        <Stat label="Log loss">{run?.log_loss?.toFixed(3) ?? '-'}</Stat>
        <Stat label="Trained">{run ? <Time value={run.finished_at} /> : '-'}</Stat>
        <Stat label="Photos">{dataset.labels.reduce((n, l) => n + l.photos, 0)}</Stat>
      </div>
      {dataset.model && dataset.model.labels.length > 0 && <p className="mt-3 text-sm text-stone-600">Model labels: {dataset.model.labels.join(', ')}</p>}
      {dataset.guilds.length > 0 && (
        <p className="mt-1 text-xs text-stone-500">Used by {dataset.guilds.map((g) => `${g.guild_name} (${g.model_name}${g.corrections ? ', takes corrections' : ''})`).join('; ')}</p>
      )}
    </Card>
  )
}
