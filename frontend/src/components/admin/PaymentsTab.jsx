import { AlertTriangle } from 'lucide-react'
import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import ErrorBanner from '../common/ErrorBanner'
import LoadingSpinner from '../common/LoadingSpinner'
import { Section, StatGrid, StatTile, Table, num, when } from './ui'

const inr = (paise) => `₹${(paise / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`

export default function PaymentsTab() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getAdminPayments().then(setData).catch((err) => setError(api.extractErrorMessage(err)))
  }, [])

  if (error) return <ErrorBanner message={error} onDismiss={() => setError(null)} />
  if (!data) return <LoadingSpinner size={16} className="text-slate-400" />
  const status = (s) => data.by_status[s] || { count: 0, amount_paise: 0, credits: 0 }
  const captured = status('captured')

  return (
    <div className="space-y-6">
      {data.test_mode && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200">
          <AlertTriangle size={15} /> Razorpay TEST MODE: these are test payments, no real money moved.
        </div>
      )}
      <StatGrid>
        <StatTile label="Captured" value={num(captured.count)} hint={`${inr(captured.amount_paise)} (test)`} />
        <StatTile label="Credits sold" value={num(captured.credits)} />
        <StatTile
          label="Pending"
          value={num(status('created').count + status('verified').count)}
          hint="awaiting the signed webhook"
        />
        <StatTile label="Failed" value={num(status('failed').count)} />
      </StatGrid>
      <Section title="Recent payments">
        <Table
          columns={[
            { key: 'created_at', label: 'When', render: (p) => when(p.created_at), className: 'whitespace-nowrap text-xs text-slate-400' },
            { key: 'order_id', label: 'Order', className: 'font-mono text-xs' },
            { key: 'pack_id', label: 'Pack' },
            { key: 'credits', label: 'Credits', align: 'right' },
            { key: 'amount', label: 'Amount', align: 'right', render: (p) => inr(p.amount_paise) },
            { key: 'status', label: 'Status', className: 'text-xs' },
          ]}
          rows={data.recent}
          rowKey={(p) => p.order_id}
          empty="No payments yet."
        />
      </Section>
    </div>
  )
}
