import { useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble'

export default function MessageList({ messages }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-center text-slate-500">
        <p className="text-sm">Upload a document, then ask a question about it.</p>
        <p className="mt-1 text-xs text-slate-600">Answers are grounded in your uploaded knowledge base with citations.</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 px-4 py-4">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
      <div ref={endRef} />
    </div>
  )
}
