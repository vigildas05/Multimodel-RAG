export const pct = (x) => (x === null || x === undefined ? '–' : `${(x * 100).toFixed(1)}%`)
export const ms = (x) => (x === null || x === undefined ? '–' : `${Math.round(x)} ms`)
export const num = (x) => (x === null || x === undefined ? '–' : x.toLocaleString())
export const when = (iso) => new Date(iso).toLocaleString()

export const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']

export const selectCls =
  'rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 focus:border-emerald-500 focus:outline-none'
