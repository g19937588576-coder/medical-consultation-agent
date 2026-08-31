const TRIAGE_META = {
  green: { label: '🟢 可自行观察', cls: 'bg-green-100 text-green-800 border-green-200' },
  yellow: { label: '🟡 建议尽快就医', cls: 'bg-yellow-100 text-yellow-800 border-yellow-200' },
  red: { label: '🔴 需立即急诊', cls: 'bg-red-100 text-red-700 border-red-200' },
}

export default function MessageBubble({ msg, streaming }) {
  const isUser = msg.role === 'user'
  const meta = msg.triage_level ? TRIAGE_META[msg.triage_level] : null
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[78%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
          isUser ? 'bg-teal-600 text-white rounded-br-sm' : 'bg-white border border-gray-200 rounded-bl-sm'
        }`}
      >
        <div className="whitespace-pre-wrap">{msg.content}{streaming && <span className="inline-block w-2 h-4 ml-0.5 bg-teal-500 animate-pulse align-middle" />}</div>
        {meta && (
          <div className={`mt-2 inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${meta.cls}`}>
            {meta.label}
          </div>
        )}
        {!isUser && msg.citations && msg.citations.length > 0 && (
          <div className="mt-2 border-t border-gray-100 pt-2">
            <div className="text-xs font-medium text-gray-400 mb-1">参考资料</div>
            {msg.citations.map((c, i) => (
              <a
                key={i}
                href={c.url}
                target="_blank"
                rel="noreferrer"
                className="block text-xs text-teal-600 hover:underline truncate mb-0.5"
              >
                [{i + 1}] {c.title}（{c.source}）
              </a>
            ))}
          </div>
        )}
        {!isUser && (
          <div className="mt-2 text-[10px] text-gray-300">以上内容仅供健康信息参考，不能替代医生面诊</div>
        )}
      </div>
    </div>
  )
}
