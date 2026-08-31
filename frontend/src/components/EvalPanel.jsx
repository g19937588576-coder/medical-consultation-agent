import { useState } from 'react'
import { runEval } from '../api.js'

const CATEGORY_LABELS = {
  symptom: '症状咨询',
  drug: '药品查询',
  literature: '文献检索',
  emergency: '紧急情况',
  adversarial: '对抗性输入',
}

function StatCard({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 text-center">
      <div className="text-2xl font-bold text-teal-700">{value}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
      {sub && <div className="text-[10px] text-gray-400 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function EvalPanel() {
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  const run = async () => {
    setRunning(true)
    setError('')
    setResult(null)
    try {
      const r = await runEval()
      setResult(r)
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto">
        <h2 className="text-lg font-bold text-gray-800">📊 评测面板</h2>
        <p className="text-sm text-gray-500 mt-1">内置 15 道评测题（症状 / 药品 / 文献 / 紧急 / 对抗），一键运行并评分。运行约需 1-2 分钟。</p>
        <button
          onClick={run}
          disabled={running}
          className="mt-4 rounded-xl bg-teal-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-50 transition"
        >
          {running ? '正在运行评测…（约 1-2 分钟）' : '▶ 开始评测'}
        </button>
        {error && <p className="mt-3 text-sm text-red-600">运行失败：{error}</p>}

        {result && (
          <>
            <div className="mt-6 grid grid-cols-4 gap-3">
              <StatCard label="通过率" value={`${Math.round((result.overall.accuracy || 0) * 100)}%`} sub={`${result.overall.pass}/${result.overall.total}`} />
              <StatCard label="引用覆盖率" value={`${Math.round((result.overall.citation_coverage || 0) * 100)}%`} />
              <StatCard label="平均相关性" value={result.overall.avg_relevance ?? '—'} sub="满分 5 分" />
              <StatCard label="评测题目数" value={result.overall.total} />
            </div>

            <div className="mt-6 grid grid-cols-5 gap-3">
              {Object.entries(result.categories).map(([cat, s]) => (
                <div key={cat} className="rounded-xl border border-gray-200 bg-white p-3 text-center">
                  <div className="text-sm font-medium text-gray-700">{CATEGORY_LABELS[cat] || cat}</div>
                  <div className="text-lg font-bold text-teal-700 mt-1">{Math.round((s.accuracy || 0) * 100)}%</div>
                  <div className="text-[10px] text-gray-400">通过 {s.pass}/{s.total}</div>
                </div>
              ))}
            </div>

            <div className="mt-6 overflow-hidden rounded-xl border border-gray-200 bg-white">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-left text-xs text-gray-500">
                    <th className="px-3 py-2">类别</th>
                    <th className="px-3 py-2">问题</th>
                    <th className="px-3 py-2 w-20">结果</th>
                    <th className="px-3 py-2 w-20">引用数</th>
                    <th className="px-3 py-2 w-16">相关性</th>
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((it, i) => (
                    <tr key={i} className="border-t border-gray-100 align-top">
                      <td className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{CATEGORY_LABELS[it.category] || it.category}</td>
                      <td className="px-3 py-2 text-xs">
                        <div className="font-medium text-gray-700">{it.question}</div>
                        <div className="text-gray-400 mt-1 line-clamp-2">{it.summary}</div>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${it.pass ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'}`}>
                          {it.pass ? '通过' : '未过'}
                        </span>
                        <div className="text-[10px] text-gray-400 mt-1">{it.note}</div>
                      </td>
                      <td className="px-3 py-2 text-center text-xs">{it.citations}</td>
                      <td className="px-3 py-2 text-center text-xs">{it.relevance ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
