import { FileAudio, FileImage, FileText } from 'lucide-react'
import { formatCitation } from '../../utils/formatCitation'

const TYPE_ICON = { pdf: FileText, image: FileImage, audio: FileAudio }

export default function SourcesPanel({ sources, retrieval }) {
  if (!sources || sources.length === 0) return null

  return (
    <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900/60 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Retrieved Sources</span>
        {retrieval && (
          <span className="text-[11px] text-slate-600">
            top_k={retrieval.top_k} · {retrieval.hybrid ? 'hybrid' : 'dense'} ·{' '}
            {retrieval.reranked ? `reranked to ${retrieval.reranked_chunks}` : 'no rerank'}
          </span>
        )}
      </div>
      <ul className="space-y-1.5">
        {sources.map((source) => {
          const Icon = TYPE_ICON[source.file_type] || FileText
          return (
            <li key={`${source.chunk_id}-${source.index}`} className="flex items-start gap-2 text-xs">
              <Icon size={13} className="mt-0.5 shrink-0 text-slate-500" />
              <div className="min-w-0">
                <div className="text-slate-300">{formatCitation(source)}</div>
                <div className="truncate text-slate-600" title={source.text_snippet}>
                  {source.text_snippet}
                </div>
              </div>
              <span className="ml-auto shrink-0 font-mono text-slate-600">{source.score.toFixed(2)}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
