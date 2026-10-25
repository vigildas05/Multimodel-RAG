import { AlertTriangle, ArrowLeft, Coins, CreditCard } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import * as api from '../api/client'
import ErrorBanner from '../components/common/ErrorBanner'
import LoadingSpinner from '../components/common/LoadingSpinner'
import { useAuth } from '../context/useAuth'

const CHECKOUT_SRC = 'https://checkout.razorpay.com/v1/checkout.js'

const REASON_LABELS = {
  signup_bonus: 'Welcome credits',
  demo_bonus: 'Demo credits',
  purchase: 'Purchase',
  query: 'Question',
  query_failed: 'Refund: question failed',
  pdf_processing: 'PDF processing',
  image_processing: 'Image processing',
  audio_processing: 'Audio transcription',
  processing_failed: 'Refund: processing failed',
  upload_failed: 'Refund: upload failed',
}

function loadCheckoutScript() {
  if (window.Razorpay) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = CHECKOUT_SRC
    script.onload = resolve
    script.onerror = () => reject(new Error('Could not load Razorpay Checkout.'))
    document.body.appendChild(script)
  })
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

export default function BillingPage() {
  const { user } = useAuth()
  const [billing, setBilling] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busyPack, setBusyPack] = useState(null)

  const refresh = useCallback(async () => {
    const data = await api.getBilling()
    setBilling(data)
    return data
  }, [])

  useEffect(() => {
    refresh().catch((err) => setError(api.extractErrorMessage(err)))
  }, [refresh])

  // Credits are granted only when Razorpay's signed webhook reaches the server, so after a
  // successful checkout we wait for the order to show up as captured.
  const awaitCredits = async (orderId) => {
    for (let i = 0; i < 20; i++) {
      const data = await refresh()
      if (data.payments.some((p) => p.order_id === orderId && p.status === 'captured')) return true
      await wait(2000)
    }
    return false
  }

  const buy = async (pack) => {
    setError(null)
    setNotice(null)
    setBusyPack(pack.id)
    try {
      await loadCheckoutScript()
      const order = await api.createOrder(pack.id)
      const checkout = new window.Razorpay({
        key: order.key_id,
        order_id: order.order_id,
        amount: order.amount_paise,
        currency: order.currency,
        name: 'Sentinel RAG',
        description: `${pack.credits} credits (test mode)`,
        theme: { color: '#059669' },
        handler: async (response) => {
          try {
            await api.verifyCheckout(response)
            setNotice('Payment received. Waiting for confirmation from Razorpay…')
            const credited = await awaitCredits(order.order_id)
            setNotice(
              credited
                ? `${pack.credits} credits added.`
                : 'Payment confirmed by checkout; credits will appear when Razorpay notifies the server.',
            )
          } catch (err) {
            setError(api.extractErrorMessage(err))
          } finally {
            setBusyPack(null)
          }
        },
        modal: { ondismiss: () => setBusyPack(null) },
      })
      checkout.on('payment.failed', (resp) => {
        setError(resp?.error?.description || 'Payment failed.')
        setBusyPack(null)
      })
      checkout.open()
    } catch (err) {
      setError(api.extractErrorMessage(err))
      setBusyPack(null)
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 px-4 py-6 text-slate-100">
      <div className="mx-auto max-w-3xl space-y-5">
        <Link to="/" className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
          <ArrowLeft size={14} /> Back to workspace
        </Link>

        <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>
            <strong>TEST MODE: use Razorpay test cards, no real money.</strong> For example card 4111 1111 1111 1111,
            any future expiry, any CVV. Nothing you enter here is charged.
          </span>
        </div>

        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
        {notice && <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">{notice}</div>}

        {!billing ? (
          <LoadingSpinner size={18} className="text-slate-400" />
        ) : (
          <>
            <section className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-500">Balance</p>
                <p className="mt-1 flex items-center gap-2 text-3xl font-semibold">
                  <Coins size={24} className="text-emerald-400" /> {billing.balance} credits
                </p>
              </div>
              <div className="text-right text-xs text-slate-400">
                <p>Question: {billing.costs.query} credit</p>
                <p>PDF: {billing.costs.pdf} · Image: {billing.costs.image} · Audio: {billing.costs.audio}</p>
                <p className="text-slate-500">Failed actions are refunded automatically.</p>
              </div>
            </section>

            <section>
              <h2 className="mb-2 text-sm font-semibold text-slate-300">Buy credits</h2>
              {!billing.payments_enabled && (
                <p className="mb-2 text-xs text-slate-500">Payments are not configured on this server.</p>
              )}
              {user.is_demo && <p className="mb-2 text-xs text-slate-500">Create a free account to buy credits.</p>}
              <div className="grid gap-3 sm:grid-cols-3">
                {billing.packs.map((pack) => (
                  <div key={pack.id} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                    <p className="text-sm font-medium">{pack.name}</p>
                    <p className="mt-1 text-2xl font-semibold">{pack.credits} credits</p>
                    <p className="text-sm text-slate-400">₹{pack.price_inr} (test)</p>
                    <button
                      onClick={() => buy(pack)}
                      disabled={!billing.payments_enabled || user.is_demo || busyPack !== null}
                      className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {busyPack === pack.id ? <LoadingSpinner size={14} /> : <CreditCard size={14} />}
                      Buy
                    </button>
                  </div>
                ))}
              </div>
            </section>

            <section>
              <h2 className="mb-2 text-sm font-semibold text-slate-300">History</h2>
              <div className="overflow-hidden rounded-xl border border-slate-800">
                <table className="w-full text-sm">
                  <thead className="bg-slate-900 text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">When</th>
                      <th className="px-3 py-2 text-left font-medium">What</th>
                      <th className="px-3 py-2 text-right font-medium">Credits</th>
                    </tr>
                  </thead>
                  <tbody>
                    {billing.history.length === 0 && (
                      <tr>
                        <td colSpan={3} className="px-3 py-3 text-center text-xs text-slate-500">No activity yet.</td>
                      </tr>
                    )}
                    {billing.history.map((entry, i) => (
                      <tr key={i} className="border-t border-slate-800/70">
                        <td className="px-3 py-2 text-xs text-slate-400">{new Date(entry.created_at).toLocaleString()}</td>
                        <td className="px-3 py-2">{REASON_LABELS[entry.reason] || entry.reason}</td>
                        <td className={`px-3 py-2 text-right font-mono ${entry.amount > 0 ? 'text-emerald-400' : 'text-slate-300'}`}>
                          {entry.amount > 0 ? '+' : ''}
                          {entry.amount}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            {billing.payments.length > 0 && (
              <section>
                <h2 className="mb-2 text-sm font-semibold text-slate-300">Payments</h2>
                <ul className="space-y-1 text-sm">
                  {billing.payments.map((p) => (
                    <li key={p.order_id} className="flex justify-between rounded-lg bg-slate-900/60 px-3 py-2">
                      <span>
                        {p.credits} credits · ₹{(p.amount_paise / 100).toFixed(0)} {p.test_mode && <span className="text-amber-300">(test)</span>}
                      </span>
                      <span className="text-xs text-slate-400">{p.status}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  )
}
