import { useQuery } from '@tanstack/react-query'
import { api, query } from '../../api/client'
import type { Schemas } from '../../api/types'

export type Dataset = Schemas['DatasetOut']
export type ImageRow = Schemas['ImageOut']
export type RunSummary = Schemas['RunSummary']
export type RunDetail = Schemas['RunDetail']

/** Every classifier query starts with this key, so one invalidation refreshes the page. */
export const CLASSIFIER = ['classifier'] as const

export const thumbUrl = (id: number) => `/api/classifier/images/${id}/file?size=thumb`
export const largeUrl = (id: number) => `/api/classifier/images/${id}/file?size=large`

export function useDatasets() {
  return useQuery({ queryKey: [...CLASSIFIER, 'datasets'], queryFn: () => api<Dataset[]>('GET', '/api/classifier/datasets') })
}

export type PhotoFilter = { label?: string; removed: boolean; source?: string; offset: number }

export const PAGE_SIZE = 100

export function useImages(dataset: string, filter: PhotoFilter) {
  const path = `/api/classifier/datasets/${dataset}/images${query({
    label: filter.label,
    source: filter.source,
    removed: filter.removed,
    limit: PAGE_SIZE,
    offset: filter.offset,
  })}`
  return useQuery({ queryKey: [...CLASSIFIER, 'images', path], queryFn: () => api<Schemas['Page_ImageOut_']>('GET', path) })
}

export function useRuns(dataset: string) {
  return useQuery({
    queryKey: [...CLASSIFIER, 'runs', dataset],
    queryFn: () => api<Schemas['Page_RunSummary_']>('GET', `/api/classifier/datasets/${dataset}/runs?limit=50`),
  })
}

export function useRun(runId: number | undefined) {
  return useQuery({
    queryKey: [...CLASSIFIER, 'run', runId],
    queryFn: () => api<RunDetail>('GET', `/api/classifier/runs/${runId}`),
    enabled: runId !== undefined,
  })
}

export type Change = { label?: string; removed?: boolean }

export function changeImages(ids: number[], change: Change) {
  return api<Schemas['Changed']>('POST', '/api/classifier/images/bulk', { ids, ...change })
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : `${Math.round(value * 1000) / 10}%`
}

// The shape of a run's `results`, as training.py writes it. Runs from before cross validation scored a
// held-out val set: they have no method, folds or unscored_labels, and have unseen_val_labels instead.
export type Prediction = { image_id: number; label: string; predicted: string; correct: boolean; fold?: number; probabilities: Record<string, number> }
export type Results = {
  method?: 'cross_validation'
  folds?: number
  labels: string[]
  // train: photos the model was trained on. val: photos that were scored.
  per_label: Record<string, { train: number; val: number; precision: number | null; recall: number | null }>
  confusion: number[][]
  predictions: Prediction[]
  skipped: number[]
  unscored_labels?: string[]
  unseen_val_labels?: string[]
}

/** How a run's score reads in a sentence, like "92.7% right on 331 photos it hadn't studied". */
export function scoreText(run: RunSummary): string {
  if (run.accuracy === null) return 'nothing to score'
  return `${percent(run.accuracy)} right on ${run.val_count} ${run.method === 'cross_validation' ? "photos it hadn't studied" : 'val photos'}`
}

/** What the latest run guessed for each photo it scored, by photo id. Empty for runs that scored a val set. */
export function useLatestGuesses(dataset: Dataset): Map<number, Prediction> {
  const run = useRun(dataset.latest_run?.method === 'cross_validation' ? dataset.latest_run.id : undefined)
  const results = run.data?.results as Results | null | undefined
  return new Map((results?.predictions ?? []).map((p) => [p.image_id, p]))
}
