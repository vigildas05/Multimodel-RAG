import { useReducer } from 'react'
import { AppContext, DEFAULT_SETTINGS } from './useAppContext'

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

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  return <AppContext.Provider value={{ state, dispatch }}>{children}</AppContext.Provider>
}


