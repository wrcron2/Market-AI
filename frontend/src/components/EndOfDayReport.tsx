import { useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Download, Link2, Check, FileText, Clock, AlertTriangle } from 'lucide-react'

interface EODReport {
  date: string
  markdown: string
  equity: number
  daily_pnl: number
  daily_pnl_pct: number
  trades_count: number
  win_rate: number
  open_positions_count: number
  created_at: number
}

const pnlColor = (v: number) => (v >= 0 ? 'text-emerald-400' : 'text-red-400')
const pnlSign = (v: number) => (v >= 0 ? '+' : '')

function fmtUSD(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function EndOfDayReport({ refreshToken }: { refreshToken?: number }) {
  const [report, setReport] = useState<EODReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [copied, setCopied] = useState(false)

  const load = useCallback(() => {
    const date = new URLSearchParams(window.location.search).get('date')
    const url = date ? `/api/reports/eod?date=${encodeURIComponent(date)}` : '/api/reports/eod/latest'
    setLoading(true)
    setNotFound(false)
    fetch(url)
      .then((r) => {
        if (r.status === 404) { setNotFound(true); return null }
        return r.ok ? r.json() : null
      })
      .then((d) => { if (d) setReport(d) })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load, refreshToken])

  const handleDownload = () => {
    if (!report) return
    const blob = new Blob([report.markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `marketflow-eod-report-${report.date}.md`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  const handleShare = async () => {
    if (!report) return
    const link = `${window.location.origin}${window.location.pathname}?tab=reports&date=${report.date}`
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard unavailable — silently ignore */
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-line bg-surface px-4 py-6 text-sm text-ink-muted">
        <Clock size={18} className="animate-spin" />
        Loading end-of-day report...
      </div>
    )
  }

  if (notFound || !report) {
    return (
      <div className="rounded-xl border border-line bg-surface px-6 py-10 text-center">
        <FileText size={28} className="mx-auto mb-3 text-ink-faint" />
        <div className="text-sm font-semibold text-ink-muted">No end-of-day report yet</div>
        <div className="mt-1 text-[12px] text-ink-faint">
          A report is generated automatically after the market closes and the day's trading finishes.
        </div>
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line-soft px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-signal-blue/10 text-blue-400">
            <FileText size={17} />
          </div>
          <div>
            <div className="text-sm font-semibold">End-of-Day Report</div>
            <div className="text-[12px] text-ink-faint">{report.date}</div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <div className="text-right">
            <div className={`font-mono text-[15px] font-semibold ${pnlColor(report.daily_pnl)}`}>
              {pnlSign(report.daily_pnl)}${fmtUSD(Math.abs(report.daily_pnl))}
            </div>
            <div className="text-[10px] uppercase tracking-wide text-ink-faint">
              {pnlSign(report.daily_pnl_pct)}{report.daily_pnl_pct.toFixed(2)}% today
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleDownload}
              title="Download report as Markdown"
              className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-surface-sunken px-3 py-1.5 text-[12px] font-medium text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
            >
              <Download size={13} />
              Download
            </button>
            <button
              onClick={handleShare}
              title="Copy a shareable link to this report"
              className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-surface-sunken px-3 py-1.5 text-[12px] font-medium text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
            >
              {copied ? <Check size={13} className="text-emerald-400" /> : <Link2 size={13} />}
              {copied ? 'Copied' : 'Share'}
            </button>
          </div>
        </div>
      </div>

      <div className="mf-eod-markdown px-5 py-4 text-[13px] leading-relaxed text-ink-muted">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: () => null, // title is rendered in the header above
            h2: ({ children }) => (
              <h3 className="mb-2 mt-5 text-[13px] font-semibold uppercase tracking-wide text-ink-faint first:mt-0">
                {children}
              </h3>
            ),
            h3: ({ children }) => <div className="mb-3 text-[12px] text-ink-faint">{children}</div>,
            p: ({ children }) => <p className="mb-3 text-ink-muted">{children}</p>,
            ul: ({ children }) => <ul className="mb-3 ml-4 list-disc space-y-1 text-ink-muted">{children}</ul>,
            strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
            table: ({ children }) => (
              <div className="mb-4 overflow-x-auto rounded-lg border border-line-faint">
                <table className="w-full text-[12.5px]">{children}</table>
              </div>
            ),
            thead: ({ children }) => <thead className="bg-surface-sunken">{children}</thead>,
            th: ({ children }) => (
              <th className="whitespace-nowrap px-3 py-2 text-left text-[10.5px] font-semibold uppercase tracking-wide text-ink-faint">
                {children}
              </th>
            ),
            td: ({ children }) => (
              <td className="whitespace-nowrap border-t border-line-faint px-3 py-2 font-mono text-[12.5px] text-ink-muted">
                {children}
              </td>
            ),
          }}
        >
          {report.markdown}
        </ReactMarkdown>
      </div>

      <div className="flex items-center gap-2 border-t border-line-soft px-5 py-2.5 text-[11px] text-ink-faint">
        <AlertTriangle size={12} className="text-ink-faint" />
        Auto-generated by the AI brain after market close — verify before relying on it for decisions.
      </div>
    </div>
  )
}
