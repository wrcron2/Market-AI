import { Cpu } from 'lucide-react'

/**
 * LLMProviderToggle — AWS Bedrock is disabled; provider is permanently local Ollama.
 * To re-enable: restore AWS credentials in .env and revert use_aws in router.py.
 */
export function LLMProviderToggle() {
  return (
    <div className="flex gap-2.5">
      <div className="flex-1 cursor-default rounded-lg border border-signal-blue bg-signal-blue/8 p-3">
        <div className="flex items-center gap-2 text-[13px] font-semibold">
          <Cpu size={14} /> Ollama (local)
        </div>
        <div className="mt-0.5 text-[11px] text-ink-faint">GPU · pipeline default</div>
      </div>
      <div className="flex-1 cursor-not-allowed rounded-lg border border-line-soft p-3 opacity-60">
        <div className="text-[13px] font-semibold text-ink-muted">AWS Bedrock</div>
        <div className="mt-0.5 text-[11px] text-ink-faint">disabled</div>
      </div>
    </div>
  )
}

// Stub hook — provider is permanently local, no network call needed.
export function useLLMProvider() {
  return {
    provider: 'local' as const,
    changeProvider: (_: 'aws' | 'local') => {},
    loading: false,
  }
}
