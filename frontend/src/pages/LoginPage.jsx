import { useLocation, useNavigate } from 'react-router-dom'
import AuthForm, { AuthLink } from '../components/auth/AuthForm'
import TryDemoButton from '../components/auth/TryDemoButton'
import { useAuth } from '../context/useAuth'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const handleSubmit = async (email, password) => {
    await login(email, password)
    // Only ever redirect to an in-app path (no open redirect via router state).
    const from = location.state?.from
    navigate(typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') ? from : '/', { replace: true })
  }

  return (
    <AuthForm
      title="Sign in to Sentinel RAG"
      subtitle="Ask questions across your PDFs, images and audio."
      submitLabel="Sign in"
      passwordAutoComplete="current-password"
      onSubmit={handleSubmit}
      belowForm={<TryDemoButton />}
      validate={({ email, password }) => (!email.trim() || !password ? 'Enter your email and password.' : null)}
      footer={
        <>
          No account yet? <AuthLink to="/register">Create one</AuthLink>
        </>
      }
    />
  )
}
