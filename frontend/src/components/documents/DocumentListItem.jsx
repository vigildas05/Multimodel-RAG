import { FileAudio, FileImage, FileText, Trash2 } from 'lucide-react'
import ProcessingStatusBadge from './ProcessingStatusBadge'

const TYPE_ICON = { pdf: FileText, image: FileImage, audio: FileAudio }

export default function DocumentListItem({ document, onDelete }) {
  const Icon = TYPE_ICON[document.file_type] || FileText

  return (
    <div className="group flex items-start gap-2 rounded-lg px-2 py-2 hover:bg-slate-800/60">
      <Icon size={16} className="mt-0.5 shrink-0 text-slate-400" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm text-slate-200" title={document.filename}>
            {document.filename}
          </span>
          <ProcessingStatusBadge status={document.status} />
        </div>
        <div className="text-xs text-slate-500">
          {document.status === 'failed'
            ? document.error_message || 'Processing failed'
            : `${document.num_chunks} chunk${document.num_chunks === 1 ? '' : 's'}${
                document.used_ocr ? ' · OCR' : ''
              }`}
        </div>
      </div>
      <button
        onClick={() => onDelete(document.document_id)}
        className="shrink-0 text-slate-600 opacity-0 hover:text-red-400 group-hover:opacity-100"
        title="Delete document"
      >
        <Trash2 size={14} />
      </button>
    </div>
  )
}
