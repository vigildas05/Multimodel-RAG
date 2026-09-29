import { createContext, useContext, useReducer } from 'react'

const DEFAULT_SETTINGS = {
  retrieval_top_k: 10,
  final_context_chunks: 4,
  chunk_size: 800,
  chunk_overlap: 100,
  similarity_threshold: null,
  use_reranking: true,
  use_hybrid_search: true,
  temperature: 0.2,
  top_p: 0.9,
  max_output_tokens: 800,
}

const initialState = {
  documents: [],
  messages: [], // { id, role: 'user' | 'assistant', content, sources?, retrieval?, error? }
  settings: DEFAULT_SETTINGS,
}

function reducer(state, action) {
  switch (action.type) {
    case 'SET_DOCUMENTS':
      return { ...state, documents: action.payload }
    case 'ADD_MESSAGE':
      return { ...state, messages: [...state.messages, action.payload] }
    case 'UPDATE_MESSAGE':
      return {
        ...state,
        messages: state.messages.map((m) => (m.id === action.payload.id ? { ...m, ...action.payload.patch } : m)),
      }
    case 'CLEAR_MESSAGES':
      return { ...state, messages: [] }
    case 'UPDATE_SETTINGS':
      return { ...state, settings: { ...state.settings, ...action.payload } }
    default:
      return state
  }
}

const AppContext = createContext(null)

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  return <AppContext.Provider value={{ state, dispatch }}>{children}</AppContext.Provider>
}

export function useAppContext() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useAppContext must be used within AppProvider')
  return ctx
}

export { DEFAULT_SETTINGS }
