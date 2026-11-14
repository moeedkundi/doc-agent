'use client'

import { AlertTriangle, Clock, History, Info } from 'lucide-react'
import { useEffect, useState } from 'react'

import { cn } from '@/lib/utils'

const TONES = {
  danger: 'border-destructive/50 bg-danger-soft text-danger-foreground',
  warning: 'border-warning/50 bg-warning-soft text-warning-foreground',
  info: 'border-primary/40 bg-primary/5 text-foreground',
} as const

export function Notice({
  tone,
  title,
  children,
  className,
}: {
  tone: keyof typeof TONES
  title: string
  children?: React.ReactNode
  className?: string
}) {
  const Icon = tone === 'info' ? Info : AlertTriangle
  return (
    <div role={tone === 'danger' ? 'alert' : 'status'} className={cn('flex gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm', TONES[tone], className)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>
        <div className="font-semibold">{title}</div>
        {children && <div className="mt-0.5">{children}</div>}
      </div>
    </div>
  )
}

/** Counts down the API's Retry-After, then calls onDone so the buttons come back. */
export function RateLimitNotice({ until, onDone }: { until: number; onDone: () => void }) {
  const [left, setLeft] = useState(() => Math.max(0, Math.ceil((until - Date.now()) / 1000)))
  useEffect(() => {
    const id = setInterval(() => {
      const s = Math.max(0, Math.ceil((until - Date.now()) / 1000))
      setLeft(s)
      if (s === 0) {
        clearInterval(id)
        onDone()
      }
    }, 250)
    return () => clearInterval(id)
  }, [until, onDone])
  const mm = Math.floor(left / 60)
  const ss = String(left % 60).padStart(2, '0')
  return (
    <div role="status" className={cn('flex items-center gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm', TONES.warning)}>
      <Clock className="size-4 shrink-0" aria-hidden />
      <div className="flex-1">
        <span className="font-semibold">Slow down a little.</span> The demo allows a few runs every
        few minutes per visitor. You can start another in{' '}
        <span className="font-mono font-semibold tabular-nums">
          {mm}:{ss}
        </span>
        .
      </div>
    </div>
  )
}

const FALLBACK_REASON: Record<string, string> = {
  budget_exceeded: "Today's LLM budget for the live demo is used up, so",
  replay_only: 'Live processing is off right now, so',
}

export function ReplayBanner({ reason }: { reason: string | null }) {
  return (
    <div role="status" className="border-b border-warning/40 bg-warning-soft py-2 text-[13px] text-warning-foreground">
      <div className="container flex items-center gap-2">
        <History className="size-4 shrink-0" aria-hidden />
        <span>
          <strong>Replaying a recorded run.</strong>{' '}
          {reason && FALLBACK_REASON[reason]
            ? `${FALLBACK_REASON[reason]} this is a real earlier run of the same document, played back with its original timing. No LLM calls are made.`
            : 'This is a real earlier run of the same document, played back with its original timing. No LLM calls are made.'}
        </span>
      </div>
    </div>
  )
}
