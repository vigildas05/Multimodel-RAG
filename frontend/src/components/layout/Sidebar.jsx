import { Coins, LogOut, RotateCcw, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import * as api from '../../api/client'
import DocumentListItem from '../documents/DocumentListItem'
import UploadControl from '../documents/UploadControl'
import ErrorBanner from '../common/ErrorBanner'
import { useAppContext } from '../../context/AppContext'
import { useAuth } from '../../context/useAuth'
import { useDocuments } from '../../hooks/useDocuments'

export default function Sidebar() {
  const { documents, uploading, error, upload, remove, reset } = useDocuments()
  const { user, logout } = useAuth()
  const { state } = useAppContext()
  const [confirmingReset, setConfirmingReset] = useState(false)
  const [credits, setCredits] = useState(null)

  // Refresh the balance after uploads and after each answered question (both cost credits).
  const answered = state.messages.filter((m) => m.role === 'assistant' && !m.loading).length
  useEffect(() => {
    api.getUsage().then((u) => setCredits(u.credit_balance)).catch(() => {})
  }, [documents, answered])

  const handleReset = async () => {
    if (!confirmingReset) {
      setConfirmingReset(true)
      setTimeout(() => setConfirmingReset(false), 3000)
      return
    }
    setConfirmingReset(false)
    await reset()
  }

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-slate-800 bg-slate-900/50">
      <div className="border-b border-slate-800 px-4 py-3">
        <h1 className="text-sm font-semibold text-slate-100">Knowledge Base</h1>
        <p className="text-xs text-slate-500">PDFs, images & audio</p>
      </div>

      <div className="px-3 py-3">
        <UploadControl onUpload={upload} uploading={uploading} />
        {error && <div className="mt-2"><ErrorBanner message={error} /></div>}
      </div>

      <div className="flex-1 overflow-y-auto px-2">
        {documents.length === 0 ? (
          <p className="px-2 py-4 text-center text-xs text-slate-600">No documents yet.</p>
        ) : (
          documents.map((doc) => (
            <DocumentListItem key={doc.document_id} document={doc} onDelete={remove} />
          ))
        )}
      </div>

      <div className="space-y-2 border-t border-slate-800 px-3 py-3">
        <button
          onClick={handleReset}
          className={`flex w-full items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs transition-colors ${
            confirmingReset
              ? 'bg-red-500/20 text-red-300'
              : 'bg-slate-800/60 text-slate-400 hover:bg-slate-800'
          }`}
        >
          <RotateCcw size={13} />
          {confirmingReset ? 'Click again to confirm' : 'Delete all my documents'}
        </button>

        <Link
          to="/billing"
          className="flex items-center justify-between rounded-lg bg-slate-800/40 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
        >
          <span className="flex items-center gap-1.5">
            <Coins size={13} className="text-emerald-400" />
            {credits === null ? '…' : credits} credits
          </span>
          <span className="text-slate-500">Billing</span>
        </Link>

        <div className="flex items-center gap-2 rounded-lg px-1 py-1">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-800 text-slate-400">
            <UserRound size={14} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs text-slate-300" title={user.email}>
              {user.email}
            </p>
            {user.role === 'admin' && <p className="text-[10px] uppercase tracking-wide text-emerald-400">Admin</p>}
          </div>
          <button
            onClick={logout}
            title="Sign out"
            aria-label="Sign out"
            className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-200"
          >
            <LogOut size={14} />
          </button>
        </div>
      </div>
    </aside>
  )
}
