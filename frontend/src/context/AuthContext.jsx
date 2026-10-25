import { useCallback, useEffect, useMemo, useState } from 'react'
import * as api from '../api/client'
import { AuthContext } from './useAuth'

// status: 'loading' (restoring a session on boot) | 'authenticated' | 'anonymous'
export function AuthProvider({ children }) {
  const [auth, setAuth] = useState({ status: 'loading', user: null })

  useEffect(() => {
    let cancelled = false
    const settle = (user) => {
      if (!cancelled) setAuth(user ? { status: 'authenticated', user } : { status: 'anonymous', user: null })
    }

    // Restore an existing session from cookies. If the access token has expired, the API
    // client transparently refreshes it before /auth/me is retried.
    api
      .fetchCsrfToken()
      .then(api.getCurrentUser)
      .then(settle)
      .catch(() => settle(null))

    const unsubscribe = api.onSessionExpired(() => settle(null))
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [])

  const login = useCallback(async (email, password) => {
    const user = await api.login(email, password)
    setAuth({ status: 'authenticated', user })
  }, [])

  const register = useCallback(async (email, password) => {
    const user = await api.register(email, password)
    setAuth({ status: 'authenticated', user })
  }, [])

  const startDemo = useCallback(async () => {
    const user = await api.startDemo()
    setAuth({ status: 'authenticated', user })
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      setAuth({ status: 'anonymous', user: null })
    }
  }, [])

  const value = useMemo(
    () => ({ ...auth, login, register, startDemo, logout }),
    [auth, login, register, startDemo, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

