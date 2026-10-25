import { AlertTriangle, Bot, ShieldCheck, User } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import LoadingSpinner from '../common/LoadingSpinner'
import { withCitations } from './CitationBadge'
import SourcesPanel from './SourcesPanel'

export default function MessageBubble({ message }) {
  const isUser = message.role === 'user'
  const sourceCount = message.sources?.length || 0

  const citationComponents = {
    p: ({ children }) => <p className="mb-2 last:mb-0">{withCitations(children, sourceCount)}</p>,
    li: ({ children }) => <li>{withCitations(children, sourceCount)}</li>,
    // Never fetch images from model output: an injected image URL is a zero-click way to
    // send data to an attacker's server. (The server strips them too.)
    img: () => null,
    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-emerald-400 underline">
        {children}
      </a>
    ),
  }

  return (
    <div className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'}`}>
      {!isUser && (
        <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400">
          <Bot size={15} />
        </div>
      )}

      <div className={`max-w-2xl ${isUser ? 'order-1' : ''}`}>
        <div
          className={`rounded-2xl px-4 py-2.5 text-sm ${
            isUser ? 'bg-emerald-600 text-white' : 'bg-slate-800/80 text-slate-100'
          }`}
        >
          {message.loading ? (
            <div className="flex items-center gap-2 text-slate-400">
              <LoadingSpinner size={14} />
              <span>Retrieving & generating...</span>
            </div>
          ) : message.error ? (
            <div className="flex items-start gap-2 text-red-300">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{message.error}</span>
            </div>
          ) : isUser ? (
            <span>{message.content}</span>
          ) : (
            <div className="prose-sm prose-invert max-w-none [&_p]:m-0">
              <ReactMarkdown components={citationComponents} skipHtml>
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {!isUser && !message.loading && !message.error && <SecurityNotes security={message.security} />}
        {!isUser && !message.loading && !message.error && (
          <SourcesPanel sources={message.sources} retrieval={message.retrieval} />
        )}
      </div>

      {isUser && (
        <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-700 text-slate-300">
          <User size={15} />
        </div>
      )}
    </div>
  )
}

const PII_LABELS = { email: 'email address', phone: 'phone number', aadhaar: 'Aadhaar number', pan: 'PAN', card: 'card number' }

function SecurityNotes({ security }) {
  if (!security) return null
  const notes = []
  for (const [kind, count] of Object.entries(security.pii_redactions || {})) {
    notes.push(`${count} ${PII_LABELS[kind] || kind}${count === 1 ? '' : 's'} redacted`)
  }
  if (security.removed_citations?.length) {
    notes.push(`citation ${security.removed_citations.map((n) => `[${n}]`).join(', ')} removed (no such source)`)
  }
  if (security.removed_images) notes.push(`${security.removed_images} image${security.removed_images === 1 ? '' : 's'} removed`)
  if (security.input_flagged) notes.push('question flagged for review')
  if (!notes.length) return null
  return (
    <div className="mt-1 flex items-start gap-1 text-[11px] text-slate-500">
      <ShieldCheck size={12} className="mt-px shrink-0 text-emerald-500/70" />
      <span>Security filter: {notes.join(' · ')}</span>
    </div>
  )
}
