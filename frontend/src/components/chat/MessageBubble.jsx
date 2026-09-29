import { AlertTriangle, Bot, User } from 'lucide-react'
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
              <ReactMarkdown components={citationComponents}>{message.content}</ReactMarkdown>
            </div>
          )}
        </div>

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
