import { AlertTriangle, X } from 'lucide-react'

export default function ErrorBanner({ message, onDismiss }) {
  if (!message) return null
  return (
    <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
      <AlertTriangle size={16} className="mt-0.5 shrink-0" />
      <span className="flex-1">{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="shrink-0 text-red-300/70 hover:text-red-200">
          <X size={14} />
        </button>
      )}
    </div>
  )
}
