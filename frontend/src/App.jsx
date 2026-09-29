import ChatArea from './components/layout/ChatArea'
import Sidebar from './components/layout/Sidebar'
import SettingsPanel from './components/layout/SettingsPanel'
import { AppProvider } from './context/AppContext'

export default function App() {
  return (
    <AppProvider>
      <div className="flex h-screen w-screen overflow-hidden bg-slate-950">
        <Sidebar />
        <ChatArea />
        <SettingsPanel />
      </div>
    </AppProvider>
  )
}
