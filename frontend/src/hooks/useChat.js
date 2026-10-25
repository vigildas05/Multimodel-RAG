import { useCallback, useState } from 'react'
import * as api from '../api/client'
import { useAppContext } from '../context/useAppContext'

export function useChat() {
  const { state, dispatch } = useAppContext()
  const [loading, setLoading] = useState(false)

  const ask = useCallback(
    async (question) => {
      const userMessage = { id: crypto.randomUUID(), role: 'user', content: question }
      dispatch({ type: 'ADD_MESSAGE', payload: userMessage })

      const assistantId = crypto.randomUUID()
      dispatch({
        type: 'ADD_MESSAGE',
        payload: { id: assistantId, role: 'assistant', content: '', loading: true },
      })

      setLoading(true)
      try {
        const response = await api.sendChatMessage(question, state.settings)
        dispatch({
          type: 'UPDATE_MESSAGE',
          payload: {
            id: assistantId,
            patch: {
              loading: false,
              content: response.answer,
              sources: response.sources,
              retrieval: response.retrieval,
              security: response.security,
            },
          },
        })
      } catch (err) {
        dispatch({
          type: 'UPDATE_MESSAGE',
          payload: {
            id: assistantId,
            patch: { loading: false, error: api.extractErrorMessage(err) },
          },
        })
      } finally {
        setLoading(false)
      }
    },
    [dispatch, state.settings],
  )

  const clear = useCallback(() => dispatch({ type: 'CLEAR_MESSAGES' }), [dispatch])

  return { messages: state.messages, loading, ask, clear }
}
