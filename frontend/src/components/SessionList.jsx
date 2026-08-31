export default function SessionList({ sessions, currentId, onSelect }) {
  return (
    <div className="flex-1 overflow-y-auto px-3 py-1 space-y-1">
      {sessions.length === 0 && (
        <p className="text-xs text-gray-400 px-2 py-4 text-center">暂无历史问诊</p>
      )}
      {sessions.map((s) => (
        <button
          key={s.id}
          onClick={() => onSelect(s.id)}
          className={`w-full text-left rounded-lg px-3 py-2 text-sm transition ${
            s.id === currentId ? 'bg-teal-50 border border-teal-200' : 'hover:bg-gray-100 border border-transparent'
          }`}
        >
          <div className="truncate font-medium text-gray-700">{s.title}</div>
          <div className="truncate text-xs text-gray-400 mt-0.5">{s.last_preview || '（空）'}</div>
        </button>
      ))}
    </div>
  )
}
