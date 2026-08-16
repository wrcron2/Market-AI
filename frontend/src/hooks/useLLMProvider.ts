/**
 * useLLMProvider — stub: provider is fixed to local (Ollama) until the
 * backend re-exposes the toggle. Kept as a hook so call sites don't change.
 */
export function useLLMProvider() {
  return {
    provider: 'local' as const,
    changeProvider: (next: 'aws' | 'local') => {
      void next
    },
    loading: false,
  }
}
