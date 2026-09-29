import ChatInput from '../chat/ChatInput'
import MessageList from '../chat/MessageList'
import { useChat } from '../../hooks/useChat'

export default function ChatArea() {
  const { messages, loading, ask } = useChat()

  return (
    <main className="flex h-full flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        <MessageList messages={messages} />
      </div>
      <ChatInput onSend={ask} disabled={loading} />
    </main>
  )
}
