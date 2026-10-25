import { SlidersHorizontal } from 'lucide-react'
import Slider from '../common/Slider'
import Toggle from '../common/Toggle'
import { useAppContext } from '../../context/useAppContext'

export default function SettingsPanel() {
  const { state, dispatch } = useAppContext()
  const s = state.settings

  const update = (patch) => dispatch({ type: 'UPDATE_SETTINGS', payload: patch })

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col overflow-y-auto border-l border-slate-800 bg-slate-900/50 px-4 py-3">
      <div className="mb-2 flex items-center gap-1.5">
        <SlidersHorizontal size={14} className="text-slate-400" />
        <h2 className="text-sm font-semibold text-slate-100">Advanced Retrieval Settings</h2>
      </div>

      <div className="divide-y divide-slate-800/60">
        <div className="py-2">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Retrieval</p>
          <Slider
            label="Retrieval Top-K"
            description="Candidate chunks pulled from the vector store before reranking."
            value={s.retrieval_top_k}
            min={1}
            max={30}
            onChange={(v) => update({ retrieval_top_k: v })}
          />
          <Slider
            label="Final Context Chunks"
            description="Chunks actually sent to the LLM after reranking."
            value={s.final_context_chunks}
            min={1}
            max={10}
            onChange={(v) => update({ final_context_chunks: v })}
          />
          <Slider
            label="Similarity Threshold"
            description="Minimum dense cosine score to keep a candidate (0 = disabled)."
            value={s.similarity_threshold ?? 0}
            min={0}
            max={1}
            step={0.05}
            formatValue={(v) => (v === 0 ? 'off' : v.toFixed(2))}
            onChange={(v) => update({ similarity_threshold: v === 0 ? null : v })}
          />
        </div>

        <div className="py-2">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Chunking</p>
          <Slider
            label="Chunk Size"
            description="Applies to newly uploaded documents."
            value={s.chunk_size}
            min={200}
            max={2000}
            step={50}
            onChange={(v) => update({ chunk_size: v })}
          />
          <Slider
            label="Chunk Overlap"
            value={s.chunk_overlap}
            min={0}
            max={400}
            step={10}
            onChange={(v) => update({ chunk_overlap: v })}
          />
        </div>

        <div className="py-2">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Pipeline stages</p>
          <Toggle
            label="Reranking"
            description="Cross-encoder rerank (bge-reranker-base) for precision."
            checked={s.use_reranking}
            onChange={(v) => update({ use_reranking: v })}
          />
          <Toggle
            label="Hybrid Search"
            description="Combine dense + BM25 keyword search via RRF."
            checked={s.use_hybrid_search}
            onChange={(v) => update({ use_hybrid_search: v })}
          />
        </div>

        <div className="py-2">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Generation</p>
          <Slider
            label="Temperature"
            description="Lower = more deterministic, factual answers."
            value={s.temperature}
            min={0}
            max={1.5}
            step={0.05}
            formatValue={(v) => v.toFixed(2)}
            onChange={(v) => update({ temperature: v })}
          />
          <Slider
            label="Top-p"
            description="LLM sampling parameter — distinct from Retrieval Top-K."
            value={s.top_p}
            min={0}
            max={1}
            step={0.05}
            formatValue={(v) => v.toFixed(2)}
            onChange={(v) => update({ top_p: v })}
          />
          <Slider
            label="Max Output Tokens"
            value={s.max_output_tokens}
            min={128}
            max={2048}
            step={64}
            onChange={(v) => update({ max_output_tokens: v })}
          />
        </div>
      </div>
    </aside>
  )
}
