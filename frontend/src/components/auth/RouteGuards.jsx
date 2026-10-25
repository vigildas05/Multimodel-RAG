import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../../context/useAuth'
import LoadingSpinner from '../common/LoadingSpinner'

function FullScreenLoading() {
  return (
    <div className="flex h-screen items-center justify-center bg-slate-950 text-slate-400">
      <LoadingSpinner size={20} />
    </div>
  )
}

// Client-side guards are only for UX: the API enforces authentication on every route.
export function RequireAuth({ children }) {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') return <FullScreenLoading />
  if (status === 'anonymous') return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return children
}

export function PublicOnly({ children }) {
  const { status } = useAuth()
  if (status === 'loading') return <FullScreenLoading />
  if (status === 'authenticated') return <Navigate to="/" replace />
  return children
}
