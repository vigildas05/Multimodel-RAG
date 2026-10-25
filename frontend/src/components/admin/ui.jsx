import { AlertOctagon, AlertTriangle, Info, ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import { num } from './format'

export function Section({ title, right, children }) {
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-300">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  )
}

export function StatTile({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-100">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

export function StatGrid({ children }) {
  return <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{children}</div>
}

/**
 * One series of bars, one per day. Hovering (or focusing) a column shows its value; the
 * hit target is the full column, wider than the bar itself.
 */
export function DailyBars({ data, value, label, format = num }) {
  const [active, setActive] = useState(null)
  const max = Math.max(1, ...data.map(value))
  const shown = active === null ? null : data[active]
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-2 flex h-5 items-baseline justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="tabular-nums text-slate-300">
          {shown ? `${shown.date}: ${format(value(shown))}` : `max ${format(max)}`}
        </span>
      </div>
      <div className="flex h-28 items-end gap-0.5 border-b border-slate-700" onMouseLeave={() => setActive(null)}>
        {data.map((d, i) => {
          const v = value(d)
          return (
            <button
              key={d.date}
              type="button"
              aria-label={`${d.date}: ${format(v)}`}
              onMouseEnter={() => setActive(i)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              className="flex h-full flex-1 items-end focus:outline-none"
            >
              <span
                className={`w-full rounded-t ${active === i ? 'bg-emerald-300' : 'bg-emerald-500/80'}`}
                style={{ height: v ? `${Math.max(3, (v / max) * 100)}%` : 0 }}
              />
            </button>
          )
        })}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>{data[0]?.date}</span>
        <span>{data[data.length - 1]?.date}</span>
      </div>
    </div>
  )
}

const SEVERITY = {
  critical: { icon: AlertOctagon, cls: 'border-red-500/40 bg-red-500/10 text-red-300' },
  high: { icon: ShieldAlert, cls: 'border-orange-500/40 bg-orange-500/10 text-orange-300' },
  medium: { icon: AlertTriangle, cls: 'border-amber-500/40 bg-amber-500/10 text-amber-200' },
  low: { icon: Info, cls: 'border-sky-500/30 bg-sky-500/10 text-sky-200' },
  info: { icon: Info, cls: 'border-slate-600 bg-slate-800/60 text-slate-300' },
}

// Icon + label + color, so severity never depends on color alone.
export function SeverityBadge({ severity }) {
  const { icon: Icon, cls } = SEVERITY[severity] || SEVERITY.info
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] ${cls}`}>
      <Icon size={11} />
      {severity}
    </span>
  )
}

export function Table({ columns, rows, empty = 'Nothing yet.', rowKey }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-800">
      <table className="w-full text-sm">
        <thead className="bg-slate-900 text-xs text-slate-500">
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={`px-3 py-2 font-medium ${c.align === 'right' ? 'text-right' : 'text-left'}`}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-3 py-3 text-center text-xs text-slate-500">
                {empty}
              </td>
            </tr>
          )}
          {rows.map((row, i) => (
            <tr key={rowKey ? rowKey(row) : i} className="border-t border-slate-800/70">
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`px-3 py-2 ${c.align === 'right' ? 'text-right tabular-nums' : ''} ${c.className || ''}`}
                >
                  {c.render ? c.render(row) : row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

