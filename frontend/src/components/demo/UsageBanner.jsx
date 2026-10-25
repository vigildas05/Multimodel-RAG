import { Clock, FlaskConical } from 'lucide-react'
import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import { useAppContext } from '../../context/AppContext'

function formatRemaining(isoTimestamp) {
  const ms = new Date(isoTimestamp).getTime() - Date.now()
  if (ms <= 0) return 'expired'
  const hours = Math.floor(ms / 3_600_000)
  const minutes = Math.floor((ms % 3_600_000) / 60_000)
  return hours > 0 ? `${hours}h ${minutes}m left` : `${minutes}m left`
}

/** Shown only in demo workspaces: queries left today and time until the workspace is deleted. */
export default function UsageBanner() {
  const { state } = useAppContext()
  const [usage, setUsage] = useState(null)

  // Re-fetch after every completed exchange (message count changes).
  const messageCount = state.messages.filter((m) => !m.loading).length
  useEffect(() => {
    api.getUsage().then(setUsage).catch(() => {})
  }, [messageCount])

  if (!usage?.is_demo) return null
  const left = Math.max(usage.daily_query_limit - usage.queries_today, 0)

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-emerald-500/20 bg-emerald-500/5 px-4 py-2 text-xs text-emerald-200/90">
      <span className="flex items-center gap-1.5 font-medium">
        <FlaskConical size={13} /> Demo workspace
      </span>
      <span>
        {left} of {usage.daily_query_limit} queries left today
      </span>
      {usage.workspace_expires_at && (
        <span className="flex items-center gap-1 text-emerald-200/70">
          <Clock size={12} /> {formatRemaining(usage.workspace_expires_at)}, then everything here is deleted
        </span>
      )}
    </div>
  )
}
