import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// Session cookies are httpOnly and set by the API; the browser attaches them because of
// withCredentials. Nothing session-related is ever stored in localStorage.
const client = axios.create({ baseURL: API_BASE_URL, withCredentials: true })

const CSRF_HEADER = 'X-CSRF-Token'
const SAFE_METHODS = new Set(['get', 'head', 'options'])
// Auth endpoints whose 401s mean "wrong credentials / no session", not "access token expired".
const NO_REFRESH_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout', '/auth/csrf', '/auth/demo']

// --- CSRF token (held in memory only) -----------------------------------------------

let csrfToken = null

export async function fetchCsrfToken() {
  const { data } = await client.get('/auth/csrf')
  csrfToken = data.csrf_token
  return csrfToken
}

// Incremented after every successful refresh. Each request remembers the epoch it was sent
// in, so a 401 that arrives after a refresh already finished retries straight away
// instead of rotating the token a second time.
let sessionEpoch = 0

client.interceptors.request.use(async (config) => {
  config._epoch = sessionEpoch
  if (!SAFE_METHODS.has((config.method || 'get').toLowerCase())) {
    if (!csrfToken) await fetchCsrfToken()
    config.headers[CSRF_HEADER] = csrfToken
  }
  return config
})

// --- session expiry notifications ---------------------------------------------------

const sessionExpiredListeners = new Set()

export function onSessionExpired(listener) {
  sessionExpiredListeners.add(listener)
  return () => sessionExpiredListeners.delete(listener)
}

// --- single-flight refresh ----------------------------------------------------------

// Every refresh rotates the refresh token, and the server treats a second use of the same
// token as theft (revoking the whole session). So when several requests hit 401 at once,
// they must all wait on ONE refresh call instead of each starting their own.
let refreshInFlight = null

function refreshSession() {
  if (!refreshInFlight) {
    refreshInFlight = client
      .post('/auth/refresh', null, { _noRetry: true })
      .then(({ data }) => {
        csrfToken = data.csrf_token
        sessionEpoch += 1
        return data.user
      })
      .finally(() => {
        refreshInFlight = null
      })
  }
  return refreshInFlight
}

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const { config, response } = error
    if (!config || !response || config._noRetry || config._retried) throw error

    if (response.status === 403 && response.data?.error_type === 'csrf_failed') {
      config._retried = true
      await fetchCsrfToken()
      return client(config)
    }

    if (response.status === 401 && !NO_REFRESH_PATHS.some((p) => config.url?.startsWith(p))) {
      config._retried = true
      try {
        if (config._epoch === sessionEpoch) await refreshSession()
      } catch {
        sessionExpiredListeners.forEach((listener) => listener())
        throw error
      }
      return client(config)
    }

    throw error
  },
)

export function extractErrorMessage(error) {
  const data = error?.response?.data
  if (data?.message) {
    return data.detail ? `${data.message} (${data.detail})` : data.message
  }
  // FastAPI request-validation errors: { detail: [{ msg, loc, ... }] }
  if (Array.isArray(data?.detail)) {
    return data.detail.map((d) => d.msg).join('; ')
  }
  if (error?.message) return error.message
  return 'An unexpected error occurred.'
}

// --- auth ---------------------------------------------------------------------------

async function startSession(path, email, password) {
  const { data } = await client.post(path, { email, password })
  csrfToken = data.csrf_token
  return data.user
}

export function login(email, password) {
  return startSession('/auth/login', email, password)
}

export function register(email, password) {
  return startSession('/auth/register', email, password)
}

export async function startDemo() {
  const { data } = await client.post('/auth/demo')
  csrfToken = data.csrf_token
  return data.user
}

export async function logout() {
  try {
    await client.post('/auth/logout')
  } finally {
    csrfToken = null
  }
}

export async function getCurrentUser() {
  const { data } = await client.get('/auth/me')
  return data
}

export async function getBilling() {
  const { data } = await client.get('/billing')
  return data
}

// The client sends only a pack ID; the server decides the amount.
export async function createOrder(packId) {
  const { data } = await client.post('/billing/orders', { pack_id: packId })
  return data
}

export async function verifyCheckout(response) {
  const { data } = await client.post('/billing/verify', {
    razorpay_order_id: response.razorpay_order_id,
    razorpay_payment_id: response.razorpay_payment_id,
    razorpay_signature: response.razorpay_signature,
  })
  return data
}

export async function getUsage() {
  const { data } = await client.get('/usage/me')
  return data
}

// --- admin (the API enforces the admin role on every one of these) -------------------

export async function getSecurityEvents(params) {
  const { data } = await client.get('/admin/security-events', { params })
  return data
}

export async function getSecuritySummary(days) {
  const { data } = await client.get('/admin/security-summary', { params: { days } })
  return data
}

export async function getAdminUsage(days) {
  const { data } = await client.get('/admin/usage', { params: { days } })
  return data
}

export async function getAdminPayments() {
  const { data } = await client.get('/admin/payments')
  return data
}

export async function getRedteamReports() {
  const { data } = await client.get('/admin/redteam/reports')
  return data.reports
}

export async function getRedteamReport(name) {
  const { data } = await client.get('/admin/redteam/latest', { params: { name } })
  return data
}

// --- documents, chat, search --------------------------------------------------------

export async function uploadDocument(file, { forceOcr = false, onProgress } = {}) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await client.post('/documents/upload', formData, {
    params: { force_ocr: forceOcr },
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress,
  })
  return data.document
}

export async function listDocuments() {
  const { data } = await client.get('/documents')
  return data.documents
}

export async function deleteDocument(documentId) {
  const { data } = await client.delete(`/documents/${documentId}`)
  return data
}

export async function resetDocuments() {
  const { data } = await client.post('/documents/reset')
  return data
}

export async function getDocumentChunks(documentId) {
  const { data } = await client.get(`/documents/${documentId}/chunks`)
  return data
}

export async function sendChatMessage(question, settings) {
  const { data } = await client.post('/chat', { question, settings })
  return data
}

export async function search(query, settings) {
  const { data } = await client.post('/search', { query, settings })
  return data
}
