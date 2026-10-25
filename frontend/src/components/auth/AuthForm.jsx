import { ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { extractErrorMessage } from '../../api/client'
import ErrorBanner from '../common/ErrorBanner'
import LoadingSpinner from '../common/LoadingSpinner'

const inputClass =
  'w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 ' +
  'focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500'

/**
 * Shared email/password form for sign-in and sign-up.
 * `validate(fields)` may return an error string to block submission client-side
 * (convenience only: the server enforces the real policy).
 */
export default function AuthForm({
  title,
  subtitle,
  submitLabel,
  passwordAutoComplete,
  onSubmit,
  validate,
  footer,
  extraFields,
  belowForm,
}) {
  const [fields, setFields] = useState({ email: '', password: '', confirm: '' })
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const update = (name) => (event) => setFields((f) => ({ ...f, [name]: event.target.value }))

  const handleSubmit = async (event) => {
    event.preventDefault()
    const clientError = validate?.(fields)
    if (clientError) {
      setError(clientError)
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await onSubmit(fields.email.trim(), fields.password)
    } catch (err) {
      setError(extractErrorMessage(err))
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-400">
            <ShieldCheck size={22} />
          </div>
          <h1 className="text-lg font-semibold text-slate-100">{title}</h1>
          <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/60 p-5" noValidate>
          <div>
            <label htmlFor="email" className="mb-1 block text-xs font-medium text-slate-400">
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={fields.email}
              onChange={update('email')}
              className={inputClass}
              placeholder="you@example.com"
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-1 block text-xs font-medium text-slate-400">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete={passwordAutoComplete}
              required
              maxLength={128}
              value={fields.password}
              onChange={update('password')}
              className={inputClass}
            />
          </div>
          {extraFields?.({ fields, update, inputClass })}

          {error && <ErrorBanner message={error} />}

          <button
            type="submit"
            disabled={submitting}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting && <LoadingSpinner size={14} />}
            {submitLabel}
          </button>
        </form>

        {belowForm}
        <p className="mt-4 text-center text-sm text-slate-500">{footer}</p>
      </div>
    </div>
  )
}

export function AuthLink({ to, children }) {
  return (
    <Link to={to} className="font-medium text-emerald-400 hover:text-emerald-300">
      {children}
    </Link>
  )
}
