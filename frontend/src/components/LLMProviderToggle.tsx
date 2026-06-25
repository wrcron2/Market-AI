import { Cpu } from 'lucide-react'

// AWS Bedrock is disabled — this component shows a static badge only.
// To re-enable: restore AWS credentials in .env and revert use_aws in router.py.
export function LLMProviderToggle() {
  return (
    <div className="auto-exec-wrapper" title="AWS Bedrock disabled — using local Ollama">
      <Cpu size={14} className="auto-exec-icon" />
      <span className="auto-exec-label">AI Model</span>
      <span className="auto-exec-pill auto-exec-pill-off">🖥 Local Only</span>
    </div>
  )
}

// Stub hook — provider is permanently local, no network call needed
export function useLLMProvider() {
  return {
    provider: 'local' as const,
    changeProvider: (_: 'aws' | 'local') => {},
    loading: false,
  }
}
