import type { ReactNode } from 'react'

/**
 * AppShell — structural layout for the dashboard.
 * Slot-based so the data-owning Dashboard wires each region without prop-drilling.
 *
 *   [ sidebar ][ topbar / main (scrolls) ][ rightPanel ]
 */
export function AppShell({
  sidebar,
  topbar,
  rightPanel,
  children,
}: {
  sidebar: ReactNode
  topbar: ReactNode
  rightPanel?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="mf-root flex h-screen w-full overflow-hidden text-sm">
      {sidebar}
      <div className="flex min-w-0 flex-1 flex-col">
        {topbar}
        <main className="mf-scroll relative flex-1 overflow-y-auto bg-base">
          <div className="mx-auto max-w-[1500px] px-6 pb-20 pt-5">{children}</div>
        </main>
      </div>
      {rightPanel}
    </div>
  )
}
