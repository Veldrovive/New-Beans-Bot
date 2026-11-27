import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api } from '../../api/client'
import type { Schemas } from '../../api/types'
import { Modal } from '../../components/ConfirmDialog'
import { Pagination } from '../../components/DataTable'
import { discordUrl } from '../../components/Names'
import { Badge, Button, Card, Empty, ErrorBox, Field, fieldClass, inputClass, Loading } from '../../components/ui'
import { Time } from '../../components/Time'
import { CLASSIFIER, largeUrl, PAGE_SIZE, thumbUrl, useImages, type Dataset, type ImageRow } from './api'
import { LabelInput } from './LabelInput'
import { QuickActions } from './QuickActions'

const REMOVED = '\u0000removed'
const SOURCE_COLOR = { seed: 'stone', correction: 'blue', upload: 'purple', drop: 'amber' } as const

/** A photo opened large, with its details and the quick actions. */
export function Lightbox({ image, labels, onClose }: { image: ImageRow | null; labels: string[]; onClose: () => void }) {
  return (
    <Modal open={image !== null} title={image ? `${image.label} (${image.split})` : ''} onClose={onClose}>
      {image && (
        <>
          <img src={largeUrl(image.id)} alt={image.label} className="max-h-[60vh] w-full rounded object-contain" />
          <p className="text-xs text-stone-500">
            {image.original_name ?? 'no name'} · {image.source} · added <Time value={image.added_at} />
            {image.removed_at && <> · removed <Time value={image.removed_at} /></>}
            {image.guild_id && image.channel_id && image.message_id && (
              <>
                {' '}
                ·{' '}
                <a href={discordUrl(image.guild_id, image.channel_id, image.message_id)} target="_blank" rel="noreferrer">
                  Discord message
                </a>
              </>
            )}
          </p>
          <QuickActions ids={[image.id]} labels={labels} removed={!!image.removed_at} onDone={onClose} compact />
        </>
      )}
    </Modal>
  )
}

type UploadResult = { name: string; status: 'added' | 'duplicate' | 'rejected' | 'uploading'; detail?: string }

/** Uploads files three at a time, one file per request (Cloudflare caps a request at 100 MB). */
function useUploads(dataset: string) {
  const queryClient = useQueryClient()
  const [results, setResults] = useState<UploadResult[]>([])
  const [busy, setBusy] = useState(false)
  const upload = async (files: File[], label: string, split: string) => {
    setBusy(true)
    setResults(files.map((f) => ({ name: f.name, status: 'uploading' })))
    let next = 0
    const worker = async () => {
      while (next < files.length) {
        const index = next++
        const file = files[index]
        const form = new FormData()
        form.append('file', file)
        form.append('label', label)
        form.append('split', split)
        let result: UploadResult
        try {
          const r = await api<Schemas['Uploaded']>('POST', `/api/classifier/datasets/${dataset}/images`, form)
          result = { name: file.name, status: r.duplicate ? 'duplicate' : 'added', detail: r.duplicate ? `already there as ${r.image.label}` : undefined }
        } catch (e) {
          result = { name: file.name, status: 'rejected', detail: e instanceof Error ? e.message : String(e) }
        }
        setResults((all) => all.map((r, i) => (i === index ? result : r)))
      }
    }
    await Promise.all([worker(), worker(), worker()])
    setBusy(false)
    await queryClient.invalidateQueries({ queryKey: CLASSIFIER })
  }
  return { results, busy, upload, clear: () => setResults([]) }
}

function UploadPanel({ labels, label, setLabel, uploads }: { labels: string[]; label: string; setLabel: (l: string) => void; uploads: ReturnType<typeof useUploads> }) {
  const input = useRef<HTMLInputElement>(null)
  const [split, setSplit] = useState('auto')
  const pick = (files: FileList | null) => {
    if (files && files.length && label.trim()) uploads.upload(Array.from(files), label.trim(), split)
  }
  return (
    <div className="mb-3 rounded-lg border border-dashed border-stone-300 p-3 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Upload into label">
          <div className="w-44">
            <LabelInput value={label} onChange={setLabel} labels={labels} />
          </div>
        </Field>
        <Field label="Split">
          <select className={inputClass} value={split} onChange={(e) => setSplit(e.target.value)}>
            <option value="auto">automatic</option>
            <option value="train">train</option>
            <option value="val">val</option>
          </select>
        </Field>
        <input ref={input} type="file" multiple accept="image/*,.heic,.heif" className="hidden" onChange={(e) => pick(e.target.files)} />
        <Button disabled={!label.trim() || uploads.busy} onClick={() => input.current?.click()}>
          Choose photos
        </Button>
        <span className="text-xs text-stone-500">or drop them on the grid. Up to 50 MB each; they're shrunk to 2048 pixels. Nothing is posted in Discord.</span>
      </div>
      {uploads.results.length > 0 && (
        <div className="mt-2">
          <ul className="max-h-40 space-y-0.5 overflow-y-auto text-xs">
            {uploads.results.map((r, i) => (
              <li key={i} className="flex gap-2">
                <Badge color={r.status === 'added' ? 'green' : r.status === 'duplicate' ? 'amber' : r.status === 'rejected' ? 'red' : 'stone'}>{r.status}</Badge>
                <span className="truncate">{r.name}</span>
                {r.detail && <span className="text-stone-500">{r.detail}</span>}
              </li>
            ))}
          </ul>
          {!uploads.busy && (
            <button className="mt-1 text-xs text-amber-800 hover:underline" onClick={uploads.clear}>
              Clear
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/** The labels on the left and the thumbnail grid for the chosen one. */
export function Photos({ dataset }: { dataset: Dataset }) {
  const [selectedLabel, setSelectedLabel] = useState<string | undefined>(dataset.labels[0]?.label)
  const [split, setSplit] = useState('')
  const [source, setSource] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [anchor, setAnchor] = useState<number | null>(null)
  const [open, setOpen] = useState<ImageRow | null>(null)
  const [uploadLabel, setUploadLabel] = useState(dataset.labels[0]?.label ?? '')
  const [dragging, setDragging] = useState(false)
  const uploads = useUploads(dataset.name)

  const removed = selectedLabel === REMOVED
  const images = useImages(dataset.name, { label: removed ? undefined : selectedLabel, removed, split: split || undefined, source: source || undefined, offset })
  const labels = dataset.labels.map((l) => l.label)
  const rows = images.data?.items ?? []

  const choose = (label: string | undefined) => {
    setSelectedLabel(label)
    setOffset(0)
    setSelected(new Set())
    if (label && label !== REMOVED) setUploadLabel(label)
  }
  const toggle = (index: number, shift: boolean) => {
    const id = rows[index].id
    setSelected((current) => {
      const next = new Set(current)
      if (shift && anchor !== null) {
        const [from, to] = anchor < index ? [anchor, index] : [index, anchor]
        for (let i = from; i <= to; i++) next.add(rows[i].id)
      } else if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setAnchor(index)
  }

  const removedCount = dataset.labels.reduce((n, l) => n + l.removed, 0)
  return (
    <Card title="Photos">
      <div className="flex flex-col gap-4 md:flex-row">
        <ul className="w-full shrink-0 space-y-0.5 text-sm md:w-48">
          <li>
            <button onClick={() => choose(undefined)} className={`w-full rounded px-2 py-1 text-left hover:bg-stone-100 ${selectedLabel === undefined ? 'bg-amber-100 font-semibold' : ''}`}>
              Every label
            </button>
          </li>
          {dataset.labels.map((l) => (
            <li key={l.label}>
              <button
                onClick={() => choose(l.label)}
                className={`flex w-full justify-between gap-2 rounded px-2 py-1 text-left hover:bg-stone-100 ${selectedLabel === l.label ? 'bg-amber-100 font-semibold' : ''}`}
              >
                <span className="truncate">{l.label}</span>
                <span className="text-xs whitespace-nowrap text-stone-500 tabular-nums">
                  {l.train} / {l.val}
                </span>
              </button>
            </li>
          ))}
          <li className="pt-2">
            <button onClick={() => choose(REMOVED)} className={`flex w-full justify-between rounded px-2 py-1 text-left text-stone-600 hover:bg-stone-100 ${removed ? 'bg-amber-100 font-semibold' : ''}`}>
              <span>Removed</span>
              <span className="text-xs tabular-nums">{removedCount}</span>
            </button>
          </li>
          <li className="px-2 pt-2 text-xs text-stone-400">train / val</li>
        </ul>
        <div
          className={`min-w-0 flex-1 rounded-lg ${dragging ? 'ring-4 ring-amber-400' : ''}`}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            if (e.dataTransfer.files.length && uploadLabel.trim()) uploads.upload(Array.from(e.dataTransfer.files), uploadLabel.trim(), 'auto')
          }}
        >
          <UploadPanel labels={labels} label={uploadLabel} setLabel={setUploadLabel} uploads={uploads} />
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <select className={fieldClass} value={split} onChange={(e) => { setSplit(e.target.value); setOffset(0) }}>
              <option value="">train and val</option>
              <option value="train">train only</option>
              <option value="val">val only</option>
            </select>
            <select className={fieldClass} value={source} onChange={(e) => { setSource(e.target.value); setOffset(0) }}>
              <option value="">every source</option>
              <option value="seed">seed</option>
              <option value="correction">corrections</option>
              <option value="upload">uploads</option>
              <option value="drop">DM drops</option>
            </select>
            {rows.length > 0 && (
              <Button variant="ghost" onClick={() => setSelected(selected.size === rows.length ? new Set() : new Set(rows.map((r) => r.id)))}>
                {selected.size === rows.length ? 'Select none' : 'Select all on this page'}
              </Button>
            )}
          </div>
          {selected.size > 0 && (
            <div className="sticky top-0 z-10 mb-3 rounded-lg bg-amber-50 p-2 text-sm shadow">
              <div className="mb-1 font-medium">{selected.size} selected (shift-click to select a run)</div>
              <QuickActions ids={[...selected]} labels={labels} removed={removed} onDone={() => setSelected(new Set())} />
            </div>
          )}
          {images.error ? (
            <ErrorBox error={images.error} />
          ) : images.isPending ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty>No photos here. Drop some on this box to add them.</Empty>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-2">
              {rows.map((image, index) => (
                <figure key={image.id} className={`group relative overflow-hidden rounded-lg border-2 bg-stone-100 ${selected.has(image.id) ? 'border-amber-500' : 'border-transparent'}`}>
                  <button className="block w-full" onClick={(e) => (e.shiftKey ? toggle(index, true) : setOpen(image))} title={image.original_name ?? undefined}>
                    <img src={thumbUrl(image.id)} loading="lazy" alt={image.label} className="aspect-square w-full object-cover" />
                  </button>
                  <input
                    type="checkbox"
                    aria-label={`Select photo ${image.id}`}
                    className="absolute top-1.5 left-1.5 h-4 w-4"
                    checked={selected.has(image.id)}
                    onChange={() => {}}
                    onClick={(e) => toggle(index, e.shiftKey)}
                  />
                  <figcaption className="flex flex-wrap items-center gap-1 p-1 text-xs">
                    <Badge color={image.split === 'val' ? 'amber' : 'green'}>{image.split}</Badge>
                    {image.guild_id && image.channel_id && image.message_id ? (
                      <a href={discordUrl(image.guild_id, image.channel_id, image.message_id)} target="_blank" rel="noreferrer">
                        <Badge color={SOURCE_COLOR.correction}>correction</Badge>
                      </a>
                    ) : (
                      <Badge color={SOURCE_COLOR[image.source as keyof typeof SOURCE_COLOR] ?? 'stone'}>{image.source}</Badge>
                    )}
                    {selectedLabel === undefined || removed ? <span className="truncate">{image.label}</span> : null}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
          {images.data && <Pagination total={images.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />}
        </div>
      </div>
      <Lightbox image={open} labels={labels} onClose={() => setOpen(null)} />
    </Card>
  )
}
