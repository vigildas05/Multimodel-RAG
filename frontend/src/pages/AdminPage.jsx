import { ArrowLeft, ShieldCheck } from 'lucide-react'
import { Link, Navigate, NavLink, useParams } from 'react-router-dom'
import PaymentsTab from '../components/admin/PaymentsTab'
import RedteamTab from '../components/admin/RedteamTab'
import SecurityEventsTab from '../components/admin/SecurityEventsTab'
import UsageTab from '../components/admin/UsageTab'

const TABS = [
  { id: 'security', label: 'Security events', component: SecurityEventsTab },
  { id: 'usage', label: 'Usage', component: UsageTab },
  { id: 'payments', label: 'Payments', component: PaymentsTab },
  { id: 'redteam', label: 'Red-team report', component: RedteamTab },
]

export default function AdminPage() {
  const { tab = 'security' } = useParams()
  const current = TABS.find((t) => t.id === tab)
  if (!current) return <Navigate to="/admin" replace />
  const Tab = current.component

  return (
    <div className="min-h-screen bg-slate-950 px-4 py-6 text-slate-100">
      <div className="mx-auto max-w-6xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Link to="/" className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
              <ArrowLeft size={14} /> Workspace
            </Link>
            <h1 className="flex items-center gap-2 text-lg font-semibold">
              <ShieldCheck size={18} className="text-emerald-400" /> Admin dashboard
            </h1>
          </div>
          <nav className="flex flex-wrap gap-1 rounded-xl border border-slate-800 bg-slate-900/60 p-1 text-sm">
            {TABS.map((t) => (
              <NavLink
                key={t.id}
                to={`/admin/${t.id}`}
                className={() =>
                  `rounded-lg px-3 py-1.5 ${t.id === current.id ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`
                }
              >
                {t.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <Tab />
      </div>
    </div>
  )
}
