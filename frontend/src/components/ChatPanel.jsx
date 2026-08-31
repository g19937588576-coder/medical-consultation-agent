import { useCallback, useEffect, useRef, useState } from 'react'
import { chatSSE, exportPdf, getMessages } from '../api.js'
import MessageBubble from './MessageBubble.jsx'

export default function ChatPanel({ sessionId, onUpdated }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [draft, setDraft] = useState(null)
  const bottomRef = useRef(null)
  const busyRef = useRef(false)

  useEffect(() => {
    let alive = true
    setMessages([])
    setDraft(null)
    setStatus('')
    setInput('')
    getMessages(sessionId)
      .then((msgs) => { if (alive) setMessages(msgs) })
      .catch(() => {})
    return () => { alive = false }
  }, [sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, draft, status])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || busyRef.current) return
    busyRef.current = true
    setInput('')
    setBusy(true)
    setStatus('正在分析您的问题…')
    setMessages((m) => [...m, { role: 'user', content: text, citations: [], triage_level: null }])
    setDraft({ content: '', citations: [], triage_level: null })
    try {
      await chatSSE({
        sessionId,
        message: text,
        onEvent: (ev) => {
          if (ev.type === 'tool_call') setStatus(ev.label || '正在查询资料…')
          else if (ev.type === 'guardrail') setStatus('安全检测…')
          else if (ev.type === 'token') {
            setDraft((d) => ({ ...(d || {}), content: (d?.content || '') + (ev.text || '') }))
          } else if (ev.type === 'question') {
            setMessages((m) => [...m, { role: 'assistant', content: ev.text, citations: [], triage_level: null }])
            setDraft(null)
            setStatus('')
            setBusy(false)
            busyRef.current = false
          } else if (ev.type === 'result') {
            setMessages((m) => [...m, { role: 'assistant', content: ev.text, citations: ev.citations || [], triage_level: ev.triage_level }])
            setDraft(null)
            setStatus('')
            setBusy(false)
            busyRef.current = false
          } else if (ev.type === 'error') {
            setStatus('出错了：' + (ev.detail || '未知错误'))
          }
        },
      })
    } catch (e) {
      setStatus('请求失败：' + e.message)
      setDraft(null)
    } finally {
      setBusy(false)
      busyRef.current = false
      onUpdated()
    }
  }, [input, sessionId, onUpdated])

  return (
    <div className="flex-1 flex flex-col min-w-0">
      <div className="border-b border-gray-200 bg-white px-5 py-3 flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-gray-700">问诊会话 #{sessionId}</div>
          <div className="text-xs text-gray-400">症状咨询 · 药品查询 · 文献检索</div>
        </div>
        {messages.length > 0 && (
          <button
            onClick={() => exportPdf(sessionId).catch((e) => alert('导出失败：' + e.message))}
            className="rounded-lg border border-teal-600 px-3 py-1.5 text-xs font-medium text-teal-700 hover:bg-teal-50 transition"
          >
            ⬇ 导出 PDF
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {messages.length === 0 && !busy && (
          <div className="text-center text-gray-400 text-sm mt-16 space-y-2">
            <div className="text-4xl">💬</div>
            <p>您好，我是健康咨询助手。您可以说：</p>
            <p className="text-xs">“最近总是头疼，持续一周了” · “阿司匹林和布洛芬能一起吃吗” · “高血压的最新研究进展”</p>
            <p className="text-xs text-amber-600">⚠️ 紧急情况（如胸痛、呼吸困难、自杀念头）请直接拨打 120</p>
          </div>
        )}
        {messages.map((m, i) => (
          <MessageBubble key={i} msg={m} streaming={false} />
        ))}
        {draft && (
          <MessageBubble msg={{ role: 'assistant', content: draft.content || '…', citations: [], triage_level: null }} streaming />
        )}
        {status && <div className="text-xs text-gray-400 text-center">{status}</div>}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-gray-200 bg-white p-4">
        <div className="flex items-end gap-2 max-w-3xl mx-auto">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
            }}
            rows={2}
            placeholder="描述您的症状或想了解的健康问题…"
            className="flex-1 resize-none rounded-xl border border-gray-300 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
          />
          <button
            onClick={send}
            disabled={busy || !input.trim()}
            className="rounded-xl bg-teal-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-40 transition"
          >
            {busy ? '…' : '发送'}
          </button>
        </div>
        <p className="text-center text-[10px] text-gray-300 mt-2">本助手提供健康信息参考，不能替代医生诊断与治疗</p>
      </div>
    </div>
  )
}
