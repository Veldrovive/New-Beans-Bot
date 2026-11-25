import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'

type Toast = { id: number; kind: 'ok' | 'error'; text: string }
type Push = (kind: Toast['kind'], text: string) => void

const ToastContext = createContext<Push>(() => {})

export function useToast(): Push {
  return useContext(ToastContext)
}

let nextId = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), [])
  const push = useCallback<Push>(
    (kind, text) => {
      const id = nextId++
      setToasts((all) => [...all, { id, kind, text }])
      setTimeout(() => dismiss(id), kind === 'error' ? 10_000 : 5_000)
    },
    [dismiss],
  )
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="fixed right-4 bottom-4 z-50 flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => (
          <button
            key={t.id}
            onClick={() => dismiss(t.id)}
            className={`rounded-lg px-4 py-3 text-left text-sm shadow-lg ${
              t.kind === 'error' ? 'bg-red-700 text-white' : 'bg-stone-800 text-white'
            }`}
          >
            {t.text}
          </button>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
