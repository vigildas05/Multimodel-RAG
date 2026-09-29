export function formatCitation(source) {
  const label = source.document || 'unknown source'
  if (source.page !== null && source.page !== undefined) {
    return `[${source.index}] ${label} — Page ${source.page}`
  }
  if (source.audio_start_time !== null && source.audio_start_time !== undefined) {
    return `[${source.index}] ${label} — ${formatTimestamp(source.audio_start_time)}-${formatTimestamp(source.audio_end_time)}`
  }
  return `[${source.index}] ${label} — chunk ${source.chunk_id}`
}

export function formatTimestamp(seconds) {
  if (seconds === null || seconds === undefined) return '?'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
