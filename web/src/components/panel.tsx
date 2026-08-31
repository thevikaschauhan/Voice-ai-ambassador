import type { ReactNode } from 'react'

interface PanelProps {
  title: string
  /** Who this panel is built for (docs/07-). Shown small, beside the title. */
  audience?: string
  action?: ReactNode
  children: ReactNode
  className?: string
}

export function Panel({ title, audience, action, children, className = '' }: PanelProps) {
  return (
    <section
      className={`flex min-h-0 flex-col border border-ink-800 bg-ink-900 ${className}`}
      aria-label={title}
    >
      <header className="flex shrink-0 items-baseline justify-between gap-4 border-b border-ink-800 px-5 py-3.5">
        <h2 className="text-[13px] font-medium tracking-[0.14em] text-ink-300 uppercase">
          {title}
        </h2>
        {audience ? (
          <span className="hidden text-[11px] tracking-wide text-ink-500 sm:inline">
            {audience}
          </span>
        ) : null}
        {action}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
    </section>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-[13px] leading-relaxed text-ink-500">{children}</p>
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-6 border-b border-ink-850 py-2.5 last:border-b-0">
      <dt className="shrink-0 text-[12px] tracking-wide text-ink-500">{label}</dt>
      <dd className="text-right text-[13px] text-ink-200">{children}</dd>
    </div>
  )
}

/** Not measured is a first-class value, never a zero. */
export function NotMeasured() {
  return <span className="text-[12px] text-ink-600">not measured</span>
}
