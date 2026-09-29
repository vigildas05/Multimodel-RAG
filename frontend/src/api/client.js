import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const client = axios.create({ baseURL: API_BASE_URL })

export function extractErrorMessage(error) {
  const data = error?.response?.data
  if (data?.message) {
    return data.detail ? `${data.message} (${data.detail})` : data.message
  }
  if (error?.message) return error.message
  return 'An unexpected error occurred.'
}

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

export async function transcribeAudio(file) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await client.post('/audio/transcribe', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function analyzeImage(file) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await client.post('/images/analyze', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}
