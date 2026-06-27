import { useState, useRef, useEffect } from 'react'
import { Sparkles, ChevronRight, ArrowUp } from 'lucide-react'
import ReactMarkdown from 'react-markdown'

type Role = 'Chief PM' | 'Engineering' | 'Risk Analyst' | 'Strategy Advisor'
type AiModel = 'claude-sonnet' | 'deepseek-r1' | 'qwen3'

const MODEL_LABELS: Record<AiModel, { label: string; desc: string }> = {
  'claude-sonnet': { label: 'Claude Sonnet', desc: 'Cloud · fast · best for strategy & PM' },
  'deepseek-r1': { label: 'Llama 3.3 70B', desc: 'Groq cloud · fast · reasoning' },
  'qwen3': { label: 'Qwen3 32B', desc: 'Groq cloud · fast · thinking model' },
}

interface Message {
  role: 'user' | 'assistant'
  text: string
}

const ROLE_DESC: Record<Role, string> = {
  'Chief PM': 'Strategic decisions · PRD format · priority assessment',
  Engineering: 'Architecture · code patterns · technical trade-offs',
  'Risk Analyst': 'Position sizing · drawdown · risk metrics',
  'Strategy Advisor': 'Signal quality · backtests · entry/exit rules',
}

interface Props {
  open: boolean
  onClose: () => void
  /**
   * Wire this to your Claude (cloud) route. It receives the role, the user
   * question, and a context snapshot you should populate from live state
   * (GET /api/context-snapshot). Return the assistant's markdown reply.
   * If omitted, a canned reply is shown so the panel works standalone.
   */
  onAsk?: (args: { role: Role; question: string; model: AiModel }) => Promise<string>
  /** Floating overlay style on mobile. */
  floating?: boolean
}

export function AskAiPanel({ open, onClose, onAsk, floating = false }: Props) {
  const [role, setRole] = useState<Role>('Chief PM')
  const [model, setModel] = useState<AiModel>('claude-sonnet')
  const [input, setInput] = useState('')
  const [typing, setTyping] = useState(false)
  const [history, setHistory] = useState<Record<Role, Message[]>>({
    'Chief PM': [],
    Engineering: [],
    'Risk Analyst': [],
    'Strategy Advisor': [],
  })
  const scrollRef = useRef<HTMLDivElement>(null)
  const messages = history[role]

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, typing])

  if (!open) return null

  const send = async (q: string) => {
    const question = q.trim()
    if (!question || typing) return
    setHistory((h) => ({ ...h, [role]: [...h[role], { role: 'user', text: question }] }))
    setInput('')
    setTyping(true)
    try {
      const reply = onAsk
        ? await onAsk({ role, question, model })
        : `Connect \`onAsk\` to your backend route. Model: **${MODEL_LABELS[model].label}**. It should inject \`/api/context-snapshot\` into the system prompt for ${role}.`
      setHistory((h) => ({ ...h, [role]: [...h[role], { role: 'assistant', text: reply }] }))
    } catch {
      setHistory((h) => ({
        ...h,
        [role]: [...h[role], { role: 'assistant', text: '⚠️ Request failed. Check the Ask AI route.' }],
      }))
    } finally {
      setTyping(false)
    }
  }

  return (
    <aside
      className={`flex h-full w-[360px] shrink-0 flex-col border-l border-line-faint bg-base ${
        floating ? 'fixed right-0 top-0 z-[60] w-[min(360px,90vw)] shadow-2xl' : ''
      }`}
    >
      {/* Header */}
      <div className="flex h-[60px] shrink-0 items-center justify-between border-b border-line-faint px-4">
        <div className="flex items-center gap-2.5">
          <span className="flex h-[26px] w-[26px] items-center justify-center rounded-lg bg-gradient-to-br from-signal-blue to-signal-purple text-white">
            <Sparkles size={14} />
          </span>
          <span className="text-sm font-semibold">Ask AI</span>
        </div>
        <button
          onClick={onClose}
          className="flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-line-soft bg-surface-raised text-ink-muted hover:text-ink"
        >
          <ChevronRight size={15} />
        </button>
      </div>

      {/* Role & Model */}
      <div className="border-b border-line-faint px-3.5 py-3">
        <div className="mb-1.5 text-[11px] tracking-wide text-ink-faint">ROLE</div>
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as Role)}
          className="w-full cursor-pointer rounded-lg border border-line-soft bg-surface-raised px-2.5 py-2 text-[13px] text-ink outline-none focus:border-signal-blue"
        >
          {(Object.keys(ROLE_DESC) as Role[]).map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <div className="mt-1.5 text-[11px] leading-snug text-slate-600">{ROLE_DESC[role]}</div>

        <div className="mb-1.5 mt-3 text-[11px] tracking-wide text-ink-faint">MODEL</div>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value as AiModel)}
          className="w-full cursor-pointer rounded-lg border border-line-soft bg-surface-raised px-2.5 py-2 text-[13px] text-ink outline-none focus:border-signal-blue"
        >
          {(Object.keys(MODEL_LABELS) as AiModel[]).map((m) => (
            <option key={m} value={m}>{MODEL_LABELS[m].label}</option>
          ))}
        </select>
        <div className="mt-1.5 text-[11px] leading-snug text-slate-600">{MODEL_LABELS[model].desc}</div>
      </div>

      {/* Quick actions */}
      <div className="flex flex-wrap gap-1.5 border-b border-line-faint px-3.5 py-2.5">
        {['Review my signals', 'Check risk', 'What should I do today?'].map((qa) => (
          <button
            key={qa}
            onClick={() => send(qa)}
            className="rounded-full border border-line-soft bg-surface-raised px-2.5 py-1.5 text-[11.5px] text-ink-muted hover:border-signal-blue hover:text-blue-300"
          >
            {qa}
          </button>
        ))}
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="mf-scroll flex flex-1 flex-col gap-3 overflow-y-auto p-3.5">
        {messages.length === 0 && (
          <div className="m-auto max-w-[80%] text-center text-[12.5px] leading-relaxed text-slate-600">
            Ask the <span className="text-blue-300">{role}</span> about your live system state.
          </div>
        )}
        {messages.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="self-end">
              <div className="max-w-[85%] rounded-xl rounded-tr-sm bg-blue-900 px-3 py-2.5 text-[12.5px] leading-snug text-blue-100">
                {m.text}
              </div>
            </div>
          ) : (
            <div key={i} className="self-start">
              <div className="mb-1 text-[10px] font-semibold tracking-wide text-violet-400">{role}</div>
              <div className="prose-mf max-w-[90%] rounded-xl rounded-tl-sm border border-line-soft bg-surface-raised px-3 py-2.5 text-[12.5px] leading-relaxed text-slate-300">
                <ReactMarkdown>{m.text}</ReactMarkdown>
              </div>
            </div>
          ),
        )}
        {typing && (
          <div className="flex gap-1 self-start rounded-xl border border-line-soft bg-surface-raised px-3.5 py-2.5">
            {[0, 0.2, 0.4].map((d) => (
              <span
                key={d}
                className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-signal-purple"
                style={{ animationDelay: `${d}s` }}
              />
            ))}
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="border-t border-line-faint px-3.5 py-3">
        <div className="mb-1.5 flex justify-between text-[10px] text-slate-600">
          <span>Context: live snapshot · {MODEL_LABELS[model].label}</span>
          <span>10/min</span>
        </div>
        <div className="flex items-end gap-2 rounded-xl border border-line-soft bg-surface-raised p-2 focus-within:border-signal-blue">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send(input)
              }
            }}
            rows={1}
            placeholder="Ask a question…"
            className="max-h-[90px] flex-1 resize-none bg-transparent text-[13px] leading-normal text-ink outline-none"
          />
          <button
            onClick={() => send(input)}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-signal-blue text-white hover:bg-blue-600"
          >
            <ArrowUp size={16} />
          </button>
        </div>
      </div>
    </aside>
  )
}
