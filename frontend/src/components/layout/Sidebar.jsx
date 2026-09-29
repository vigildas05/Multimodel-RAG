import { RotateCcw } from 'lucide-react'
import { useState } from 'react'
import DocumentListItem from '../documents/DocumentListItem'
import UploadControl from '../documents/UploadControl'
import ErrorBanner from '../common/ErrorBanner'
import { useDocuments } from '../../hooks/useDocuments'

export default function Sidebar() {
  const { documents, uploading, error, upload, remove, reset } = useDocuments()
  const [confirmingReset, setConfirmingReset] = useState(false)

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

      <div className="border-t border-slate-800 px-3 py-3">
        <button
          onClick={handleReset}
          className={`flex w-full items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs transition-colors ${
            confirmingReset
              ? 'bg-red-500/20 text-red-300'
              : 'bg-slate-800/60 text-slate-400 hover:bg-slate-800'
          }`}
        >
          <RotateCcw size={13} />
          {confirmingReset ? 'Click again to confirm reset' : 'Reset all documents'}
        </button>
      </div>
    </aside>
  )
}
