import { useNavigate } from 'react-router-dom'
import AuthForm, { AuthLink } from '../components/auth/AuthForm'
import { useAuth } from '../context/useAuth'

// Mirrors the server default (PASSWORD_MIN_LENGTH). The server is the source of truth;
// this only saves a round trip.
const MIN_PASSWORD_LENGTH = 12

function validate({ email, password, confirm }) {
  if (!email.trim()) return 'Enter your email address.'
  if (password.length < MIN_PASSWORD_LENGTH) return `Password must be at least ${MIN_PASSWORD_LENGTH} characters long.`
  if (password !== confirm) return 'Passwords do not match.'
  return null
}

export default function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()

  const handleSubmit = async (email, password) => {
    await register(email, password)
    navigate('/', { replace: true })
  }

  return (
    <AuthForm
      title="Create your account"
      subtitle="You get a private workspace that only you can search."
      submitLabel="Create account"
      passwordAutoComplete="new-password"
      onSubmit={handleSubmit}
      validate={validate}
      extraFields={({ fields, update, inputClass }) => (
        <div>
          <label htmlFor="confirm" className="mb-1 block text-xs font-medium text-slate-400">
            Confirm password
          </label>
          <input
            id="confirm"
            type="password"
            autoComplete="new-password"
            required
            maxLength={128}
            value={fields.confirm}
            onChange={update('confirm')}
            className={inputClass}
          />
          <p className="mt-1.5 text-xs text-slate-500">At least {MIN_PASSWORD_LENGTH} characters. A passphrase works well.</p>
        </div>
      )}
      footer={
        <>
          Already have an account? <AuthLink to="/login">Sign in</AuthLink>
        </>
      }
    />
  )
}
