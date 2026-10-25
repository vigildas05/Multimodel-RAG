import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import LoadingSpinner from '../common/LoadingSpinner'

// Plain-language labels for the security gateway's detection categories.
const REASON_LABELS = {
  instruction_override: 'tries to override the assistant’s instructions',
  roleplay_jailbreak: 'jailbreak / role-play attempt',
  prompt_extraction: 'tries to extract the system prompt',
  data_exfiltration: 'tries to send data elsewhere',
  delimiter_injection: 'fake system / role markers',
  obfuscation: 'hidden or encoded text',
  abnormal_input: 'abnormally long text',
  classifier: 'flagged by the injection classifier',
}

/** Lists the chunks of one document that the ingest guard quarantined, and why. */
export default function QuarantinePanel({ documentId }) {
  const [chunks, setChunks] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api
      .getDocumentChunks(documentId)
      .then((data) => setChunks(data.chunks.filter((c) => c.quarantined)))
      .catch((err) => setError(api.extractErrorMessage(err)))
  }, [documentId])

  if (error) return <p className="mt-1 text-xs text-red-300">{error}</p>
  if (!chunks) return <LoadingSpinner size={12} className="mt-1 text-slate-500" />

  return (
    <div className="mt-1.5 space-y-1.5 rounded-md border border-red-500/20 bg-red-500/5 p-2">
      <p className="text-[11px] text-slate-400">
        These parts of the document look like instructions aimed at the AI, so they are never used to answer
        questions.
      </p>
      {chunks.map((chunk) => (
        <div key={chunk.chunk_id} className="rounded bg-slate-900/70 p-1.5">
          <div className="text-[11px] text-red-300">
            {chunk.page_number ? `Page ${chunk.page_number} · ` : ''}
            {chunk.quarantine_reasons.map((r) => REASON_LABELS[r] || r).join(', ')}
            {chunk.injection_risk != null && <span className="text-slate-500"> · risk {chunk.injection_risk.toFixed(2)}</span>}
          </div>
          {/* Rendered as plain text, never as HTML/markdown. */}
          <p className="mt-0.5 line-clamp-3 whitespace-pre-wrap break-words text-[11px] text-slate-400">{chunk.text}</p>
        </div>
      ))}
    </div>
  )
}
