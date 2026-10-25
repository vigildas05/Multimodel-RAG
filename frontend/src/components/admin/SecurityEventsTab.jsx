import { ChevronDown, ChevronRight } from 'lucide-react'
import { Fragment, useEffect, useState } from 'react'
import * as api from '../../api/client'
import ErrorBanner from '../common/ErrorBanner'
import LoadingSpinner from '../common/LoadingSpinner'
import { SEVERITIES, num, selectCls, when } from './format'
import { DailyBars, Section, SeverityBadge, StatGrid, StatTile, Table } from './ui'

const PAGE_SIZE = 50
const EMPTY_FILTERS = { event_type: '', source: '', severity: '', user_id: '', workspace_id: '', from: '', to: '' }
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

// Date inputs are whole UTC days; "to" is inclusive, so the API bound is the next midnight.
function toParams(filters, offset) {
  const params = { limit: PAGE_SIZE, offset }
  for (const key of ['event_type', 'source', 'severity']) if (filters[key]) params[key] = filters[key]
  for (const key of ['user_id', 'workspace_id']) if (UUID.test(filters[key].trim())) params[key] = filters[key].trim()
  if (filters.from) params.since = `${filters.from}T00:00:00Z`
  if (filters.to) {
    const next = new Date(`${filters.to}T00:00:00Z`)
    next.setUTCDate(next.getUTCDate() + 1)
    params.until = next.toISOString()
  }
  return params
}

export default function SecurityEventsTab() {
  const [days, setDays] = useState(14)
  const [summary, setSummary] = useState(null)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getSecuritySummary(days).then(setSummary).catch((err) => setError(api.extractErrorMessage(err)))
  }, [days])

  useEffect(() => {
    let cancelled = false
    api
      .getSecurityEvents(toParams(filters, offset))
      .then((data) => !cancelled && setPage(data))
      .catch((err) => !cancelled && setError(api.extractErrorMessage(err)))
    return () => {
      cancelled = true
    }
  }, [filters, offset])

  const setFilter = (key) => (event) => {
    setOffset(0)
    setExpanded(null)
    setFilters((f) => ({ ...f, [key]: event.target.value }))
  }
  const badId = (key) => filters[key].trim() && !UUID.test(filters[key].trim())

  const columns = [
    {
      key: 'expand',
      label: '',
      render: (e) => (expanded === e.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />),
      className: 'w-6 text-slate-500',
    },
    { key: 'created_at', label: 'When', render: (e) => when(e.created_at), className: 'whitespace-nowrap text-xs text-slate-400' },
    { key: 'severity', label: 'Severity', render: (e) => <SeverityBadge severity={e.severity} /> },
    { key: 'event_type', label: 'Event', className: 'font-mono text-xs' },
    { key: 'source', label: 'Source', className: 'text-xs text-slate-400' },
    { key: 'user', label: 'User', render: (e) => e.user_email || '–', className: 'max-w-[14rem] truncate text-xs' },
    { key: 'risk', label: 'Risk', align: 'right', render: (e) => (e.risk_score === null ? '–' : e.risk_score.toFixed(2)) },
  ]

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <Section
        title="Overview"
        right={
          <select className={selectCls} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Overview range">
            {[7, 14, 30, 90].map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </select>
        }
      >
        {!summary ? (
          <LoadingSpinner size={16} className="text-slate-400" />
        ) : (
          <>
            <StatGrid>
              <StatTile label="Events" value={num(summary.total)} hint={`last ${summary.days} days`} />
              <StatTile label="Critical + high" value={num((summary.by_severity.critical || 0) + (summary.by_severity.high || 0))} />
              <StatTile label="Medium" value={num(summary.by_severity.medium || 0)} />
              <StatTile label="Low + info" value={num((summary.by_severity.low || 0) + (summary.by_severity.info || 0))} />
            </StatGrid>
            <DailyBars
              data={summary.by_day}
              value={(d) => Object.values(d.counts).reduce((a, b) => a + b, 0)}
              label="Security events per day (UTC)"
            />
            <Table
              columns={[
                { key: 'event_type', label: 'Event type', className: 'font-mono text-xs' },
                { key: 'source', label: 'Source', className: 'text-xs text-slate-400' },
                { key: 'count', label: 'Count', align: 'right' },
              ]}
              rows={summary.by_type}
              rowKey={(r) => `${r.event_type}/${r.source}`}
              empty="No events in this period."
            />
          </>
        )}
      </Section>

      <Section title="Events">
        <div className="flex flex-wrap items-end gap-2 rounded-xl border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-400">
          <label className="flex flex-col gap-1">
            Event type
            <select className={selectCls} value={filters.event_type} onChange={setFilter('event_type')}>
              <option value="">All</option>
              {summary?.event_types.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            Source
            <select className={selectCls} value={filters.source} onChange={setFilter('source')}>
              <option value="">All</option>
              {summary?.sources.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            Severity
            <select className={selectCls} value={filters.severity} onChange={setFilter('severity')}>
              <option value="">All</option>
              {SEVERITIES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            From (UTC)
            <input type="date" className={selectCls} value={filters.from} onChange={setFilter('from')} />
          </label>
          <label className="flex flex-col gap-1">
            To (UTC)
            <input type="date" className={selectCls} value={filters.to} onChange={setFilter('to')} />
          </label>
          {['user_id', 'workspace_id'].map((key) => (
            <label key={key} className="flex flex-col gap-1">
              {key === 'user_id' ? 'User ID' : 'Workspace ID'}
              <input
                className={`${selectCls} w-72 font-mono ${badId(key) ? 'border-red-500/60' : ''}`}
                placeholder="UUID"
                value={filters[key]}
                onChange={setFilter(key)}
                aria-invalid={badId(key) ? 'true' : undefined}
              />
            </label>
          ))}
          <button
            onClick={() => {
              setOffset(0)
              setFilters(EMPTY_FILTERS)
            }}
            className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700"
          >
            Clear
          </button>
        </div>

        {!page ? (
          <LoadingSpinner size={16} className="text-slate-400" />
        ) : (
          <>
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
                  {page.events.length === 0 && (
                    <tr>
                      <td colSpan={columns.length} className="px-3 py-3 text-center text-xs text-slate-500">
                        No events match these filters.
                      </td>
                    </tr>
                  )}
                  {page.events.map((e) => (
                    <Fragment key={e.id}>
                      <tr
                        className="cursor-pointer border-t border-slate-800/70 hover:bg-slate-900/60"
                        onClick={() => setExpanded(expanded === e.id ? null : e.id)}
                      >
                        {columns.map((c) => (
                          <td key={c.key} className={`px-3 py-2 ${c.align === 'right' ? 'text-right tabular-nums' : ''} ${c.className || ''}`}>
                            {c.render ? c.render(e) : e[c.key]}
                          </td>
                        ))}
                      </tr>
                      {expanded === e.id && <EventDetails event={e} colSpan={columns.length} />}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>
                {page.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {num(page.total)}
              </span>
              <div className="flex gap-2">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  className="rounded-lg bg-slate-800 px-3 py-1.5 hover:bg-slate-700 disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  disabled={offset + PAGE_SIZE >= page.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  className="rounded-lg bg-slate-800 px-3 py-1.5 hover:bg-slate-700 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </Section>
    </div>
  )
}

// Event details can contain attacker-written text (a blocked question, a filename), so they
// are only ever rendered as plain text: React escapes it, and nothing is parsed as HTML/markdown.
function EventDetails({ event, colSpan }) {
  return (
    <tr className="bg-slate-950/60">
      <td colSpan={colSpan} className="px-4 py-3 text-xs">
        <dl className="mb-2 grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-slate-400">
          <dt>User ID</dt>
          <dd className="font-mono text-slate-300">{event.user_id || '–'}</dd>
          <dt>Workspace ID</dt>
          <dd className="font-mono text-slate-300">{event.workspace_id || '–'}</dd>
          <dt>Request ID</dt>
          <dd className="font-mono text-slate-300">{event.request_id || '–'}</dd>
        </dl>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-900 p-3 font-mono text-[11px] text-slate-300">
          {JSON.stringify(event.details, null, 2)}
        </pre>
      </td>
    </tr>
  )
}
