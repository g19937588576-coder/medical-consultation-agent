const BASE = '/api'

async function jfetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let text = ''
    try { text = await res.text() } catch { /* ignore */ }
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export const createSession = () => jfetch(`${BASE}/sessions`, { method: 'POST', body: '{}' })
export const listSessions = () => jfetch(`${BASE}/sessions`)
export const getMessages = (id) => jfetch(`${BASE}/sessions/${id}/messages`)
export const runEval = () => jfetch(`${BASE}/eval`)

function parseSSE(raw) {
  let type = 'message'
  let data = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) type = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try { return { type, ...JSON.parse(data) } } catch { return { type, text: data } }
}

export async function chatSSE({ sessionId, message, onEvent }) {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const event = parseSSE(raw)
      if (event) onEvent(event)
    }
  }
}

export async function exportPdf(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/export`, { method: 'POST' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `medical-consultation-${sessionId}.pdf`
  a.click()
  URL.revokeObjectURL(url)
}
