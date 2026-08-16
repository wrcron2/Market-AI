import type { TradingMode } from '../hooks/useTradingMode'

export type { TradingMode }

interface Props {
  mode: TradingMode
  onChange: (mode: TradingMode) => void
  disabled?: boolean
}

/**
 * TradingModeToggle — Yahoo Finance simulation ↔ broker path.
 * Yahoo (left): yfinance data + simulated execution, no real money.
 * Right: live data + execution through the Green Light gate — the execution
 * venue is Alpaca paper (the `ibkr` mode value is a legacy enum name).
 */
export function TradingModeToggle({ mode, onChange, disabled = false }: Props) {
  const isIBKR = mode === 'ibkr'
  return (
    <div className="flex gap-2.5">
      <button
        disabled={disabled}
        onClick={() => onChange('yahoo')}
        className={`flex-1 rounded-lg border px-3 py-2.5 text-center text-[13px] font-semibold transition-colors disabled:opacity-50 ${
          !isIBKR ? 'border-signal-blue bg-signal-blue/10 text-signal-blue' : 'border-line-soft text-ink-faint hover:border-line'
        }`}
      >
        🧪 Yahoo · Sim
      </button>
      <button
        disabled={disabled}
        onClick={() => onChange('ibkr')}
        className={`flex-1 rounded-lg border px-3 py-2.5 text-center text-[13px] font-semibold transition-colors disabled:opacity-50 ${
          isIBKR ? 'border-signal-red bg-signal-red/10 text-signal-red' : 'border-line-soft text-ink-faint hover:border-line'
        }`}
      >
        ⚡ Alpaca · Paper
      </button>
    </div>
  )
}
