import { Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import LoadingSpinner from '../common/LoadingSpinner'

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.ogg,.flac'

export default function UploadControl({ onUpload, uploading }) {
  const inputRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)

  const handleFiles = async (files) => {
    for (const file of files) {
      try {
        await onUpload(file)
      } catch {
        // error surfaced via sidebar error banner; continue with remaining files
      }
    }
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragOver(false)
        handleFiles(Array.from(e.dataTransfer.files))
      }}
      onClick={() => inputRef.current?.click()}
      className={`flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed px-3 py-4 text-center transition-colors ${
        dragOver ? 'border-emerald-400 bg-emerald-500/5' : 'border-slate-700 hover:border-slate-600'
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        multiple
        className="hidden"
        onChange={(e) => {
          handleFiles(Array.from(e.target.files))
          e.target.value = ''
        }}
      />
      {uploading ? <LoadingSpinner size={18} className="text-emerald-400" /> : <Upload size={18} className="text-slate-400" />}
      <span className="text-xs text-slate-400">
        {uploading ? 'Uploading...' : 'Drop PDF, image, or audio — or click to browse'}
      </span>
    </div>
  )
}
