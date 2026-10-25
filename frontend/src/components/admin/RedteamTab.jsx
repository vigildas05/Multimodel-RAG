import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import ErrorBanner from '../common/ErrorBanner'
import LoadingSpinner from '../common/LoadingSpinner'
import { Section, StatGrid, StatTile, Table, ms, num, pct, selectCls } from './ui'

const STOPPED_BY = {
  input_guard: 'Input guard (blocked question)',
  ingest_guard: 'Ingest guard (quarantined chunk)',
  output_guard_block: 'Output guard (blocked answer)',
  output_guard_sanitized: 'Output guard (redacted / stripped)',
}

// A share of 0..1 drawn as a thin bar behind its own label, so the number is always visible.
function ShareBar({ value }) {
  return (
    <div className="flex items-center justify-end gap-2">
      <span className="tabular-nums">{pct(value)}</span>
      <span className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-800">
        <span className="block h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, value * 100)}%` }} />
      </span>
    </div>
  )
}

export default function RedteamTab() {
  const [reports, setReports] = useState(null)
  const [name, setName] = useState('full')
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getRedteamReports().then(setReports).catch((err) => setError(api.extractErrorMessage(err)))
  }, [])

  useEffect(() => {
    setReport(null)
    api
      .getRedteamReport(name)
      .then(setReport)
      .catch((err) => setError(api.extractErrorMessage(err)))
  }, [name])

  if (reports && reports.length === 0) {
    return <p className="text-sm text-slate-400">No red-team reports found. Run <code>python redteam/run_eval.py</code>.</p>
  }

  const picker = reports && (
    <select className={selectCls} value={name} onChange={(e) => setName(e.target.value)} aria-label="Report">
      {reports.map((r) => (
        <option key={r}>{r}</option>
      ))}
    </select>
  )

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {!report ? (
        <LoadingSpinner size={16} className="text-slate-400" />
      ) : (
        <ReportView report={report} picker={picker} />
      )}
    </div>
  )
}

function ReportView({ report, picker }) {
  const off = report.attacks?.off
  const on = report.attacks?.on
  const benign = report.benign?.overall
  const cfg = report.config || {}
  const categories = Object.keys(on?.by_category || {})

  return (
    <>
      <Section title={`Report: ${report.name}`} right={picker}>
        <p className="text-xs text-slate-500">
          Generated {report.generated_at} from commit <span className="font-mono">{report.commit}</span>. LLM{' '}
          <span className="font-mono">{cfg.llm}</span>, classifier <span className="font-mono">{cfg.classifier || 'off'}</span>,
          block threshold {cfg.block_threshold}, classifier weight {cfg.classifier_weight}. Dataset: {num(report.dataset?.attacks)}{' '}
          attacks, {num(report.dataset?.benign)} benign, {num(report.dataset?.external)} external.
        </p>
        {on && (
          <StatGrid>
            <StatTile label="Attack success" value={`${pct(off?.overall.asr)} → ${pct(on.overall.asr)}`} hint="security off → on" />
            <StatTile label="Holdout ASR (on)" value={pct(on.by_split?.holdout?.asr)} hint="phrasings never used for tuning" />
            <StatTile label="Benign false positives" value={pct(benign?.fpr)} hint={`flagged ${pct(benign?.flag_rate)}`} />
            <StatTile label="Added latency p95" value={ms(on.overall.latency?.p95_ms)} hint={`mean ${ms(on.overall.latency?.mean_ms)}`} />
          </StatGrid>
        )}
      </Section>

      {on && (
        <Section title="Attack success rate by category (security on)">
          <Table
            columns={[
              { key: 'category', label: 'Category', className: 'font-mono text-xs' },
              { key: 'n', label: 'n', align: 'right' },
              { key: 'off', label: 'ASR off', align: 'right', render: (r) => pct(r.off) },
              { key: 'on', label: 'ASR on', align: 'right', render: (r) => <ShareBar value={r.on} /> },
              { key: 'blocked', label: 'Blocked / quarantined', align: 'right', render: (r) => pct(r.blocked) },
            ]}
            rows={[
              ...categories.map((c) => ({
                category: c,
                n: on.by_category[c].n,
                off: off?.by_category?.[c]?.asr,
                on: on.by_category[c].asr,
                blocked: on.by_category[c].block_rate,
              })),
              { category: 'overall', n: on.overall.n, off: off?.overall.asr, on: on.overall.asr, blocked: on.overall.block_rate },
            ]}
            rowKey={(r) => r.category}
          />
        </Section>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        {on && (
          <Section title="Which layer stopped the attack">
            <Table
              columns={[
                { key: 'layer', label: 'Layer' },
                { key: 'count', label: 'Attacks', align: 'right' },
              ]}
              rows={Object.entries(on.overall.stopped_by || {}).map(([layer, count]) => ({ layer: STOPPED_BY[layer] || layer, count }))}
              rowKey={(r) => r.layer}
            />
          </Section>
        )}
        {report.external && (
          <Section title="External benchmark (deepset, detection only)">
            <Table
              columns={[
                { key: 'label', label: 'Label' },
                { key: 'n', label: 'n', align: 'right' },
                { key: 'block_rate', label: 'Blocked', align: 'right', render: (r) => pct(r.block_rate) },
                { key: 'flag_or_block_rate', label: 'Flagged or blocked', align: 'right', render: (r) => pct(r.flag_or_block_rate) },
              ]}
              rows={Object.entries(report.external).map(([label, v]) => ({ label, ...v }))}
              rowKey={(r) => r.label}
            />
          </Section>
        )}
      </div>

      <Section title={`Attacks that still succeeded (${num(report.misses.length)})`}>
        <Table
          columns={[
            { key: 'id', label: 'ID', className: 'font-mono text-xs' },
            { key: 'category', label: 'Category', className: 'text-xs' },
            { key: 'split', label: 'Split', className: 'text-xs text-slate-400' },
            { key: 'vector', label: 'Vector', className: 'text-xs text-slate-400' },
            { key: 'goal', label: 'Goal', className: 'font-mono text-xs text-slate-400' },
          ]}
          rows={report.misses}
          rowKey={(r) => r.id}
          empty="None."
        />
      </Section>
    </>
  )
}
