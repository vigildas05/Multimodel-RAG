import { createContext, useContext } from 'react'

export const DEFAULT_SETTINGS = {
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

export const AppContext = createContext(null)

export function useAppContext() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useAppContext must be used within AppProvider')
  return ctx
}
