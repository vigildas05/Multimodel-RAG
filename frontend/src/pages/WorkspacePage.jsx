import ChatArea from '../components/layout/ChatArea'
import SettingsPanel from '../components/layout/SettingsPanel'
import Sidebar from '../components/layout/Sidebar'
import { AppProvider } from '../context/AppContext'
import { useAuth } from '../context/useAuth'

export default function WorkspacePage() {
  const { user } = useAuth()
  return (
    // Keyed by user: signing in as someone else remounts the provider, so one account's
    // chat history and document list can never linger in memory for the next.
    <AppProvider key={user.user_id}>
      <div className="flex h-screen w-screen overflow-hidden bg-slate-950">
        <Sidebar />
        <ChatArea />
        <SettingsPanel />
      </div>
    </AppProvider>
  )
}
