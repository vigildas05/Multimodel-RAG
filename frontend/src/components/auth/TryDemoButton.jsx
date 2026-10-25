import { FlaskConical } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { extractErrorMessage } from '../../api/client'
import { useAuth } from '../../context/useAuth'
import ErrorBanner from '../common/ErrorBanner'
import LoadingSpinner from '../common/LoadingSpinner'

export default function TryDemoButton() {
  const { startDemo } = useAuth()
  const navigate = useNavigate()
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState(null)

  const handleClick = async () => {
    setError(null)
    setStarting(true)
    try {
      await startDemo()
      navigate('/', { replace: true })
    } catch (err) {
      setError(extractErrorMessage(err))
      setStarting(false)
    }
  }

  return (
    <div className="mt-4 space-y-2">
      <div className="flex items-center gap-3 text-xs text-slate-600">
        <div className="h-px flex-1 bg-slate-800" />
        or
        <div className="h-px flex-1 bg-slate-800" />
      </div>
      <button
        type="button"
        onClick={handleClick}
        disabled={starting}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-emerald-500/60 hover:text-emerald-300 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {starting ? <LoadingSpinner size={14} /> : <FlaskConical size={15} />}
        Try the demo, no sign-up
      </button>
      <p className="text-center text-xs text-slate-600">
        A private copy of sample documents (a paper, a chart, a lecture clip), deleted after 24 hours.
      </p>
      {error && <ErrorBanner message={error} />}
    </div>
  )
}
