import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { useToast } from './Toasts'
import { Button } from './ui'

/** A modal that says in plain words what an action will do, with optional form fields, before doing it. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = 'Confirm',
  danger = false,
  busy = false,
  canConfirm = true,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: ReactNode
  children?: ReactNode
  confirmLabel?: string
  danger?: boolean
  busy?: boolean
  canConfirm?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault()
        if (!busy) onCancel()
      }}
      className="m-auto w-[32rem] max-w-[calc(100vw-2rem)] rounded-xl p-0 shadow-2xl backdrop:bg-black/40"
    >
      <form
        method="dialog"
        className="p-5"
        onSubmit={(e) => {
          e.preventDefault()
          if (canConfirm && !busy) onConfirm()
        }}
      >
        <h2 className="mb-3 text-lg font-semibold">{title}</h2>
        <div className="space-y-3 text-sm">{children}</div>
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant={danger ? 'danger' : 'primary'} disabled={!canConfirm || busy}>
            {busy ? 'Working...' : confirmLabel}
          </Button>
        </div>
      </form>
    </dialog>
  )
}

/**
 * Runs an API action, then refreshes the queries it affects and toasts the result. Errors toast the
 * server's message, which is already in the bot's voice.
 */
export function useAction<T, V = void>(
  run: (variables: V) => Promise<T>,
  options: { invalidate: QueryKey[]; success: (result: T) => string; onDone?: (result: T) => void },
) {
  const queryClient = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: run,
    onSuccess: async (result) => {
      toast('ok', options.success(result))
      options.onDone?.(result)
      await Promise.all(options.invalidate.map((queryKey) => queryClient.invalidateQueries({ queryKey })))
    },
    onError: (error) => toast('error', error instanceof ApiError ? error.message : String(error)),
  })
}

/** A button that opens a ConfirmDialog and runs the action when confirmed. */
export function ActionButton<T>({
  label,
  title,
  description,
  confirmLabel,
  danger,
  variant,
  run,
  invalidate,
  success,
}: {
  label: ReactNode
  title: ReactNode
  description: ReactNode
  confirmLabel?: string
  danger?: boolean
  variant?: 'default' | 'primary' | 'danger' | 'ghost'
  run: () => Promise<T>
  invalidate: QueryKey[]
  success: (result: T) => string
}) {
  const [open, setOpen] = useState(false)
  const action = useAction(run, { invalidate, success, onDone: () => setOpen(false) })
  return (
    <>
      <Button variant={variant} onClick={() => setOpen(true)}>
        {label}
      </Button>
      <ConfirmDialog
        open={open}
        title={title}
        confirmLabel={confirmLabel}
        danger={danger}
        busy={action.isPending}
        onConfirm={() => action.mutate()}
        onCancel={() => setOpen(false)}
      >
        {description}
      </ConfirmDialog>
    </>
  )
}

/** A plain modal for looking at something, with a close button. Not a form, so it can hold forms. */
export function Modal({ open, title, children, onClose }: { open: boolean; title: ReactNode; children?: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
      className="m-auto w-[48rem] max-w-[calc(100vw-2rem)] rounded-xl p-0 shadow-2xl backdrop:bg-black/40"
    >
      <div className="p-5">
        <div className="mb-3 flex items-start justify-between gap-2">
          <h2 className="text-lg font-semibold">{title}</h2>
          <Button onClick={onClose}>Close</Button>
        </div>
        <div className="space-y-3 text-sm">{children}</div>
      </div>
    </dialog>
  )
}
