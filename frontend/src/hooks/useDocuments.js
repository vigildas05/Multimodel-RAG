import { useCallback, useEffect, useState } from 'react'
import * as api from '../api/client'
import { useAppContext } from '../context/AppContext'

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
