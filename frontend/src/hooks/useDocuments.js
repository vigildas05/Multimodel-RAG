import { useCallback, useEffect, useState } from 'react'
import * as api from '../api/client'
import { useAppContext } from '../context/AppContext'

const POLL_INTERVAL_MS = 2000

export function useDocuments() {
  const { state, dispatch } = useAppContext()
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const documents = await api.listDocuments()
      dispatch({ type: 'SET_DOCUMENTS', payload: documents })
    } catch (err) {
      setError(api.extractErrorMessage(err))
    }
  }, [dispatch])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Uploads are processed in the background: poll while anything is still in flight.
  const inFlight = state.documents.some((d) => d.status === 'queued' || d.status === 'processing')
  useEffect(() => {
    if (!inFlight) return undefined
    const timer = setInterval(refresh, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [inFlight, refresh])

  const upload = useCallback(
    async (file, opts) => {
      setUploading(true)
      setError(null)
      try {
        await api.uploadDocument(file, opts)
        await refresh()
      } catch (err) {
        setError(api.extractErrorMessage(err))
        throw err
      } finally {
        setUploading(false)
      }
    },
    [refresh],
  )

  const remove = useCallback(
    async (documentId) => {
      setError(null)
      try {
        await api.deleteDocument(documentId)
        await refresh()
      } catch (err) {
        setError(api.extractErrorMessage(err))
        throw err
      }
    },
    [refresh],
  )

  const reset = useCallback(async () => {
    setError(null)
    try {
      await api.resetDocuments()
      await refresh()
    } catch (err) {
      setError(api.extractErrorMessage(err))
      throw err
    }
  }, [refresh])

  return { documents: state.documents, uploading, error, upload, remove, reset, refresh }
}
