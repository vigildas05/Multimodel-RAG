import { CheckCircle2, Loader2, XCircle } from 'lucide-react'

const STATUS_MAP = {
  ready: { icon: CheckCircle2, className: 'text-emerald-400', label: 'Ready' },
  processing: { icon: Loader2, className: 'text-amber-400 animate-spin', label: 'Processing' },
  failed: { icon: XCircle, className: 'text-red-400', label: 'Failed' },
}

export default function ProcessingStatusBadge({ status }) {
  const entry = STATUS_MAP[status] || STATUS_MAP.processing
  const Icon = entry.icon
  return (
    <span className="inline-flex items-center gap-1 text-xs" title={entry.label}>
      <Icon size={13} className={entry.className} />
    </span>
  )
}
