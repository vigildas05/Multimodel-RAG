import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import ErrorBanner from '../common/ErrorBanner'
import LoadingSpinner from '../common/LoadingSpinner'
import { ms, num, pct, selectCls } from './format'
import { DailyBars, Section, StatGrid, StatTile, Table } from './ui'

export default function UsageTab() {
  const [days, setDays] = useState(14)
  const [usage, setUsage] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getAdminUsage(days).then(setUsage).catch((err) => setError(api.extractErrorMessage(err)))
  }, [days])

  if (error) return <ErrorBanner message={error} onDismiss={() => setError(null)} />
  if (!usage) return <LoadingSpinner size={16} className="text-slate-400" />
  const { totals, latency_ms: latency } = usage

  return (
    <div className="space-y-6">
      <Section
        title="Queries"
        right={
          <select className={selectCls} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Usage range">
            {[7, 14, 30, 90].map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </select>
        }
      >
        <StatGrid>
          <StatTile label="Queries" value={num(totals.queries)} hint={`${num(totals.active_users)} active users`} />
          <StatTile
            label="Blocked"
            value={num(totals.blocked)}
            hint={totals.queries ? `${pct(totals.blocked / totals.queries)} of queries` : null}
          />
          <StatTile label="Credits spent" value={num(totals.credits_spent)} hint="net of refunds" />
          <StatTile
            label="LLM tokens"
            value={num(totals.prompt_tokens + totals.completion_tokens)}
            hint={`${num(totals.prompt_tokens)} in · ${num(totals.completion_tokens)} out`}
          />
        </StatGrid>
        <div className="grid gap-3 md:grid-cols-2">
          <DailyBars data={usage.by_day} value={(d) => d.queries} label="Queries per day (UTC)" />
          <DailyBars data={usage.by_day} value={(d) => d.credits_spent} label="Credits spent per day (UTC)" />
        </div>
      </Section>

      <Section title="Latency (end to end)">
        <StatGrid>
          <StatTile label="Mean" value={ms(latency.mean)} />
          <StatTile label="p95" value={ms(latency.p95)} />
          <StatTile label="Security mean" value={ms(latency.security_mean)} hint="input + output guards" />
          <StatTile
            label="Retrieval / LLM mean"
            value={`${ms(latency.retrieval_mean)} / ${ms(latency.generation_mean)}`}
          />
        </StatGrid>
        <DailyBars data={usage.by_day} value={(d) => d.p95_ms || 0} format={ms} label="p95 latency per day" />
      </Section>

      <div className="grid gap-6 md:grid-cols-2">
        <Section title="Top users by queries">
          <Table
            columns={[
              { key: 'email', label: 'User', className: 'max-w-[16rem] truncate text-xs' },
              { key: 'queries', label: 'Queries', align: 'right' },
              { key: 'blocked', label: 'Blocked', align: 'right' },
            ]}
            rows={usage.top_users}
            rowKey={(r) => r.email}
            empty="No queries in this period."
          />
        </Section>
        <Section title="Credit movements">
          <Table
            columns={[
              { key: 'kind', label: 'Kind', className: 'text-xs text-slate-400' },
              { key: 'reason', label: 'Reason', className: 'font-mono text-xs' },
              { key: 'amount', label: 'Credits', align: 'right', render: (r) => (r.amount > 0 ? `+${num(r.amount)}` : num(r.amount)) },
            ]}
            rows={usage.credits_by_reason}
            rowKey={(r) => `${r.kind}/${r.reason}`}
            empty="No credit activity in this period."
          />
        </Section>
      </div>

      <Section title="Models">
        <Table
          columns={[
            { key: 'provider', label: 'Provider', className: 'text-xs text-slate-400' },
            { key: 'model', label: 'Model', className: 'font-mono text-xs' },
            { key: 'queries', label: 'Queries', align: 'right' },
          ]}
          rows={usage.models}
          rowKey={(r) => `${r.provider}/${r.model}`}
          empty="No answered queries in this period."
        />
      </Section>
    </div>
  )
}
