import { useCallback, useEffect, useState } from 'react'
import { createSession, listSessions } from './api.js'
import ChatPanel from './components/ChatPanel.jsx'
import SessionList from './components/SessionList.jsx'
import EvalPanel from './components/EvalPanel.jsx'

export default function App() {
  const [sessions, setSessions] = useState([])
  const [currentId, setCurrentId] = useState(null)
  const [view, setView] = useState('chat')

  const refresh = useCallback(async () => {
    try {
      const list = await listSessions()
      setSessions(list)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    const applyHash = () => {
      const m = window.location.hash.match(/^#\/s\/(\d+)/)
      if (m) {
        setCurrentId(Number(m[1]))
        setView('chat')
      }
    }
    applyHash()
    window.addEventListener('hashchange', applyHash)
    return () => window.removeEventListener('hashchange', applyHash)
  }, [])

  const handleNew = async () => {
    const rec = await createSession()
    setCurrentId(rec.id)
    setView('chat')
    window.location.hash = `#/s/${rec.id}`
    refresh()
  }

  const handleSelect = (id) => {
    setCurrentId(id)
    setView('chat')
    window.location.hash = `#/s/${id}`
  }

  return (
    <div className="flex h-screen bg-gray-50 text-gray-800">
      <aside className="w-64 shrink-0 border-r border-gray-200 bg-white flex flex-col">
        <div className="p-4 border-b border-gray-200">
          <h1 className="text-lg font-bold text-teal-700">🩺 医疗问诊 AI 助手</h1>
          <p className="text-xs text-gray-400 mt-1">健康信息咨询 · 非诊疗</p>
        </div>
        <div className="p-3">
          <button
            onClick={handleNew}
            className="w-full rounded-lg bg-teal-600 py-2 text-sm font-medium text-white hover:bg-teal-700 transition"
          >
            ＋ 新建问诊
          </button>
        </div>
        <SessionList sessions={sessions} currentId={currentId} onSelect={handleSelect} />
        <div className="p-3 border-t border-gray-200">
          <button
            onClick={() => setView(view === 'eval' ? 'chat' : 'eval')}
            className="w-full rounded-lg border border-teal-600 py-2 text-sm font-medium text-teal-700 hover:bg-teal-50 transition"
          >
            {view === 'eval' ? '← 返回问诊' : '📊 评测面板'}
          </button>
        </div>
      </aside>
      <main className="flex-1 flex flex-col min-w-0">
        {view === 'eval' ? (
          <EvalPanel />
        ) : currentId ? (
          <ChatPanel key={currentId} sessionId={currentId} onUpdated={refresh} />
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-400">
            <div className="text-center space-y-2">
              <div className="text-5xl">🩺</div>
              <p>点击「新建问诊」开始一次健康咨询</p>
              <p className="text-xs">本助手仅提供健康信息参考，不能替代医生诊断</p>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}


