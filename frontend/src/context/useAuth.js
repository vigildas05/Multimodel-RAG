import { createContext, useContext } from 'react'

// Kept apart from AuthProvider so that file exports only a component (Vite Fast Refresh
// can't hot-swap modules that mix components with other exports).
export const AuthContext = createContext(null)

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
