import { useState, useEffect, useMemo, Fragment } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  FileText, Download, ChevronDown, ChevronRight, Clock, AlertTriangle,
} from 'lucide-react'
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, Cell,
} from 'recharts'

interface Findings {
  red: number
  yellow: number
  green: number
}

interface StatusReportSummary {
  date: string
  headline: string
  equity: number
  all_time_return_pct: number
  signals_pending: number
  oldest_pending_days: number | null
  largest_position_pct_equity: number
  largest_position_symbol: string
  findings: Findings
  red_total_prior: number | null
  red_resolved_from_prior: number | null
}

function fmtDate(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function fmtUSD(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function resolutionPct(s: StatusReportSummary): number | null {
  if (s.red_total_prior === null || s.red_total_prior === 0 || s.red_resolved_from_prior === null) return null
  return (s.red_resolved_from_prior / s.red_total_prior) * 100
}

function resolutionColor(pct: number): string {
  if (pct >= 50) return 'bg-signal-green'
  if (pct >= 20) return 'bg-signal-yellow'
  return 'bg-signal-red'
}

export function StatusReportsTable() {
  const [reports, setReports] = useState<StatusReportSummary[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [openDate, setOpenDate] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/reports/status')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setReports(d.reports ?? []) })
      .catch(() => setReports([]))
      .finally(() => setLoading(false))
  }, [])

  const handleDownload = async (date: string) => {
    const res = await fetch(`/api/reports/status/${date}/markdown`)
    if (!res.ok) return
    const text = await res.text()
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `status-report-${date}.md`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-line bg-surface px-4 py-6 text-sm text-ink-muted">
        <Clock size={18} className="animate-spin" />
        Loading status reports...
      </div>
    )
  }

  if (!reports || reports.length === 0) {
    return (
      <div className="rounded-xl border border-line bg-surface px-6 py-10 text-center">
        <FileText size={28} className="mx-auto mb-3 text-ink-faint" />
        <div className="text-sm font-semibold text-ink-muted">No Chief PM status reports yet</div>
        <div className="mt-1 text-[12px] text-ink-faint">
          Run <span className="font-mono">/chief-pm</span> to generate a live audit report.
        </div>
      </div>
    )
  }

  const sorted = [...reports].sort((a, b) => (a.date < b.date ? 1 : -1))

  return (
    <div>
      <h3 className="mb-3 flex items-center gap-2.5 text-base font-semibold">
        <FileText size={16} />
        Chief PM Status Reports
      </h3>
      <div className="overflow-hidden rounded-xl border border-line bg-surface">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-line-soft">
                {['Date', 'Headline', 'Findings', 'Gap Resolution', '', ''].map(c => (
                  <th key={c} className="whitespace-nowrap px-4 py-2.5 text-left text-[10.5px] font-semibold uppercase tracking-wide text-ink-faint">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map(s => {
                const isOpen = openDate === s.date
                const pct = resolutionPct(s)
                return (
                  <Fragment key={s.date}>
                    <tr className="border-b border-line-faint hover:bg-surface-hover">
                      <td className="whitespace-nowrap px-4 py-3 font-mono text-[13px] font-semibold">{fmtDate(s.date)}</td>
                      <td className="max-w-md px-4 py-3 text-[13px] text-ink-muted" title={s.headline}>
                        <span className="line-clamp-2">{s.headline}</span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <span className="rounded bg-signal-red/15 px-1.5 py-0.5 text-[10px] font-semibold text-signal-red">{s.findings.red}R</span>
                          <span className="rounded bg-signal-yellow/15 px-1.5 py-0.5 text-[10px] font-semibold text-signal-yellow">{s.findings.yellow}Y</span>
                          <span className="rounded bg-signal-green/15 px-1.5 py-0.5 text-[10px] font-semibold text-signal-green">{s.findings.green}G</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        {pct === null ? (
                          <span className="text-[11px] text-ink-faint">Baseline</span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-sunken">
                              <div className={`h-full rounded-full ${resolutionColor(pct)}`} style={{ width: `${Math.max(pct, 4)}%` }} />
                            </div>
                            <span className="text-[11px] font-mono text-ink-muted">
                              {s.red_resolved_from_prior}/{s.red_total_prior} ({pct.toFixed(0)}%)
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <button
                          onClick={() => handleDownload(s.date)}
                          title="Download raw markdown"
                          className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-surface-sunken px-2.5 py-1.5 text-[11px] font-medium text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
                        >
                          <Download size={12} />
                          Download
                        </button>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <button
                          onClick={() => setOpenDate(isOpen ? null : s.date)}
                          className="flex items-center gap-1 rounded-lg border border-line-soft bg-surface-sunken px-2.5 py-1.5 text-[11px] font-medium text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
                        >
                          {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                          Insight
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={6} className="border-b border-line-faint bg-surface-sunken/40 p-0">
                          <ReportDrilldown allReports={sorted} report={s} onDownload={handleDownload} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function ReportDrilldown({ allReports, report, onDownload }: {
  allReports: StatusReportSummary[]
  report: StatusReportSummary
  onDownload: (date: string) => void
}) {
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [loadingMd, setLoadingMd] = useState(true)

  useEffect(() => {
    setLoadingMd(true)
    fetch(`/api/reports/status/${report.date}/markdown`)
      .then(r => r.ok ? r.text() : null)
      .then(setMarkdown)
      .catch(() => setMarkdown(null))
      .finally(() => setLoadingMd(false))
  }, [report.date])

  // Series up to and including this report, oldest first, for trend charts.
  const series = useMemo(
    () => allReports.filter(r => r.date <= report.date).sort((a, b) => a.date < b.date ? -1 : 1),
    [allReports, report.date],
  )

  const equitySeries = series.map(r => ({ date: fmtDate(r.date), equity: r.equity }))
  const backlogSeries = series.map(r => ({ date: fmtDate(r.date), pending: r.signals_pending }))
  const concentrationSeries = series.map(r => ({
    date: fmtDate(r.date), pct: r.largest_position_pct_equity, symbol: r.largest_position_symbol,
  }))
  const findingsSeries = series.map(r => ({
    date: fmtDate(r.date), Red: r.findings.red, Yellow: r.findings.yellow, Green: r.findings.green,
  }))
  const resolutionSeries = series
    .filter(r => resolutionPct(r) !== null)
    .map(r => ({ date: fmtDate(r.date), pct: resolutionPct(r)! }))

  return (
    <div className="space-y-5 px-5 py-5">
      {/* Summary stat row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <DrilldownStat label="Equity" value={`$${fmtUSD(report.equity)}`} />
        <DrilldownStat label="All-Time Return" value={`${report.all_time_return_pct >= 0 ? '+' : ''}${report.all_time_return_pct.toFixed(2)}%`} />
        <DrilldownStat label="Pending Signals" value={String(report.signals_pending)} />
        <DrilldownStat
          label="Largest Position"
          value={`${report.largest_position_symbol} ${report.largest_position_pct_equity.toFixed(1)}%`}
        />
      </div>

      {/* Trend charts — only worth rendering once there's more than one data point */}
      {series.length > 1 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <MiniChartCard title="Equity Trend">
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={equitySeries} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => `$${(v / 1000).toFixed(0)}k`} width={44} />
                <Tooltip
                  contentStyle={{ background: '#161e2e', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                  formatter={(v: any) => [`$${fmtUSD(Number(v))}`, 'Equity']}
                />
                <Line type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={2} dot={{ r: 4, fill: '#3b82f6' }} />
              </LineChart>
            </ResponsiveContainer>
          </MiniChartCard>

          <MiniChartCard title="Signal Backlog (Pending)">
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={backlogSeries} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} width={36} />
                <Tooltip
                  contentStyle={{ background: '#161e2e', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                  formatter={(v: any) => [v, 'Pending signals']}
                />
                <Bar dataKey="pending" fill="#f97316" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </MiniChartCard>

          <MiniChartCard title="Largest Position Concentration (% of Equity)">
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={concentrationSeries} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => `${v}%`} width={36} />
                <Tooltip
                  contentStyle={{ background: '#161e2e', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                  formatter={(v: any, _n: any, p: any) => [`${Number(v).toFixed(1)}% (${p.payload.symbol})`, 'Largest position']}
                />
                <Line type="monotone" dataKey="pct" stroke="#7c3aed" strokeWidth={2} dot={{ r: 4, fill: '#7c3aed' }} />
              </LineChart>
            </ResponsiveContainer>
          </MiniChartCard>

          <MiniChartCard title="Findings Mix (Red / Yellow / Green)">
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={findingsSeries} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} width={28} allowDecimals={false} />
                <Tooltip contentStyle={{ background: '#161e2e', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Red" stackId="f" fill="#ef4444" />
                <Bar dataKey="Yellow" stackId="f" fill="#eab308" />
                <Bar dataKey="Green" stackId="f" fill="#22c55e" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </MiniChartCard>

          {resolutionSeries.length > 0 && (
            <MiniChartCard title="Gap Resolution Rate (vs. prior report)">
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={resolutionSeries} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                  <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => `${v}%`} width={36} />
                  <Tooltip
                    contentStyle={{ background: '#161e2e', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                    formatter={(v: any) => [`${Number(v).toFixed(0)}%`, 'Resolved']}
                  />
                  <Bar dataKey="pct" radius={[4, 4, 0, 0]}>
                    {resolutionSeries.map((d, i) => <Cell key={i} fill={d.pct >= 50 ? '#22c55e' : d.pct >= 20 ? '#eab308' : '#ef4444'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div className="mt-1 text-[10.5px] text-ink-faint">Green ≥50% of prior RED findings resolved · Yellow 20–49% · Red &lt;20%</div>
            </MiniChartCard>
          )}
        </div>
      )}

      {/* Full report markdown */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-[13px] font-semibold text-ink-muted">Full Report</h4>
          <button
            onClick={() => onDownload(report.date)}
            className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-surface-sunken px-2.5 py-1.5 text-[11px] font-medium text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
          >
            <Download size={12} />
            Download
          </button>
        </div>
        {loadingMd ? (
          <div className="flex items-center gap-2 text-[12px] text-ink-faint">
            <Clock size={14} className="animate-spin" /> Loading report...
          </div>
        ) : !markdown ? (
          <div className="flex items-center gap-2 text-[12px] text-ink-faint">
            <AlertTriangle size={14} /> Could not load report content.
          </div>
        ) : (
          <div className="mf-eod-markdown max-h-[420px] overflow-y-auto rounded-lg border border-line-faint bg-surface px-4 py-3 text-[12.5px] leading-relaxed text-ink-muted">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h2: ({ children }) => <h3 className="mb-2 mt-4 text-[12.5px] font-semibold uppercase tracking-wide text-ink-faint first:mt-0">{children}</h3>,
                h3: ({ children }) => <div className="mb-2 text-[12px] font-semibold text-ink">{children}</div>,
                p: ({ children }) => <p className="mb-2.5 text-ink-muted">{children}</p>,
                ul: ({ children }) => <ul className="mb-2.5 ml-4 list-disc space-y-1 text-ink-muted">{children}</ul>,
                strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
                table: ({ children }) => (
                  <div className="mb-3 overflow-x-auto rounded-lg border border-line-faint">
                    <table className="w-full text-[11.5px]">{children}</table>
                  </div>
                ),
                thead: ({ children }) => <thead className="bg-surface-sunken">{children}</thead>,
                th: ({ children }) => <th className="whitespace-nowrap px-2.5 py-1.5 text-left text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{children}</th>,
                td: ({ children }) => <td className="whitespace-nowrap border-t border-line-faint px-2.5 py-1.5 font-mono text-[11.5px] text-ink-muted">{children}</td>,
              }}
            >
              {markdown}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}

function DrilldownStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line-faint bg-surface px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</div>
      <div className="mt-1 font-mono text-[15px] font-semibold text-ink">{value}</div>
    </div>
  )
}

function MiniChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line-faint bg-surface p-3">
      <div className="mb-2 text-[11.5px] font-semibold text-ink-muted">{title}</div>
      {children}
    </div>
  )
}
