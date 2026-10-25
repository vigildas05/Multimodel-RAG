import { EyeOff, FileAudio, FileImage, FileText, ShieldAlert, Trash2 } from 'lucide-react'
import { useState } from 'react'
import ProcessingStatusBadge from './ProcessingStatusBadge'
import QuarantinePanel from './QuarantinePanel'

const TYPE_ICON = { pdf: FileText, image: FileImage, audio: FileAudio }

export default function DocumentListItem({ document, onDelete }) {
  const Icon = TYPE_ICON[document.file_type] || FileText
  const [showQuarantine, setShowQuarantine] = useState(false)

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
            : document.status === 'queued'
              ? 'Waiting to be processed…'
              : document.status === 'processing'
                ? 'Processing…'
                : `${document.num_chunks} chunk${document.num_chunks === 1 ? '' : 's'}${
                document.used_ocr ? ' · OCR' : ''
              }`}
        </div>
        {document.hidden_span_count > 0 && (
          <div
            className="mt-0.5 flex items-center gap-1 text-xs text-amber-400/90"
            title="Text a reader couldn't see (white-on-white, tiny, invisible or off-page) was removed before indexing, because hidden text is a common way to smuggle instructions to an AI."
          >
            <EyeOff size={11} />
            {document.hidden_span_count} hidden text span{document.hidden_span_count === 1 ? '' : 's'} removed
          </div>
        )}
        {document.quarantined_chunk_count > 0 && (
          <>
            <button
              onClick={() => setShowQuarantine((v) => !v)}
              className="mt-0.5 flex items-center gap-1 text-xs text-red-300/90 hover:text-red-200"
            >
              <ShieldAlert size={11} />
              {document.quarantined_chunk_count} chunk{document.quarantined_chunk_count === 1 ? '' : 's'} quarantined
              <span className="text-slate-500">· {showQuarantine ? 'hide' : 'why?'}</span>
            </button>
            {showQuarantine && <QuarantinePanel documentId={document.document_id} />}
          </>
        )}
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
