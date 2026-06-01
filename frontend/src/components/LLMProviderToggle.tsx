import { useState, useEffect, useCallback } from 'react'
import { Cloud, Cpu } from 'lucide-react'

type Provider = 'aws' | 'local'

interface Props {
  provider: Provider
  onChange: (provider: Provider) => void
  disabled?: boolean
}

export function LLMProviderToggle({ provider, onChange, disabled = false }: Props) {
  const isAws = provider === 'aws'

  const handleToggle = () => {
    if (disabled) return
    onChange(isAws ? 'local' : 'aws')
  }

  return (
    <div className={`auto-exec-wrapper ${disabled ? 'auto-exec-disabled' : ''} ${isAws ? 'auto-exec-active' : ''}`}>
      {isAws ? <Cloud size={14} className="auto-exec-icon" /> : <Cpu size={14} className="auto-exec-icon" />}
      <span className={`auto-exec-label ${isAws ? 'auto-exec-label-on' : ''}`}>
        AI Model
      </span>

      <button
        role="switch"
        aria-checked={isAws}
        aria-label="Switch between AWS Bedrock and local Ollama model"
        className={`mode-toggle-track ${isAws ? 'auto-exec-track-on' : 'mode-toggle-off'}`}
        onClick={handleToggle}
        disabled={disabled}
      >
        <span className="mode-toggle-thumb" />
      </button>

      {isAws ? (
        <span className="auto-exec-pill auto-exec-pill-on">☁️ AWS</span>
      ) : (
        <span className="auto-exec-pill auto-exec-pill-off">🖥 Local</span>
      )}
    </div>
  )
}

export function useLLMProvider() {
  const [provider, setProvider] = useState<Provider>('local')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/llm-provider')
      .then((r) => r.json())
      .then((data: { provider: Provider }) => setProvider(data.provider ?? 'local'))
      .catch(() => setProvider('local'))
      .finally(() => setLoading(false))
  }, [])

  const changeProvider = useCallback(async (next: Provider) => {
    setProvider(next)
    try {
      const res = await fetch('/api/llm-provider', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: next }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data: { provider: Provider } = await res.json()
      setProvider(data.provider)
    } catch {
      setProvider((prev) => (prev === 'aws' ? 'local' : 'aws'))
    }
  }, [])

  return { provider, changeProvider, loading }
}
