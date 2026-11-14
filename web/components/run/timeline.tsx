'use client'

import { Check, ChevronRight, Loader2, Pause, Triangle, X } from 'lucide-react'
import { useState } from 'react'

import { JsonBlock } from '@/components/json-block'
import { Badge } from '@/components/ui/badge'
import { int, ms, shortModel, usd } from '@/lib/format'
import { isStrongCall, stepTotals, type Step } from '@/lib/trace'
import type { TraceEvent } from '@/lib/types'
import { cn } from '@/lib/utils'

const SERVER: Record<string, string> = {
  classify_document: 'extraction',
  extract_fields: 'extraction',
  reextract_fields: 'extraction',
  validate_record: 'validation',
  match_invoice_to_po: 'validation',
  lookup_vendor: 'reference',
  save_record: 'sqlstore',
  get_purchase_order: 'sqlstore',
  save_match_result: 'sqlstore',
  run_select: 'sqlstore',
  describe_schema: 'sqlstore',
}

export function Timeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="relative space-y-2 before:absolute before:bottom-2 before:left-[11px] before:top-2 before:w-px before:bg-border">
      {steps.map((step) => (
        <StepItem key={step.key} step={step} />
      ))}
    </ol>
  )
}

function StepItem({ step }: { step: Step }) {
  const [open, setOpen] = useState(false)
  const t = stepTotals(step)
  const tools = [...new Set(step.calls.map((c) => c.tool).filter(Boolean))].join(' · ')
  const flagged = stepFlag(step)
  const id = `step-${step.key}`
  const expandable = step.calls.length > 0 || step.error !== null
  return (
    <li className="relative pl-[34px]">
      <StepDot step={step} flagged={flagged !== null && flagged.tone !== 'success'} />
      <div
        className={cn(
          'overflow-hidden rounded-lg border bg-card',
          step.strong && 'border-warning/70 shadow-[inset_3px_0_0_var(--color-warning)]',
          step.status === 'paused' &&
            'border-primary/60 shadow-[inset_3px_0_0_var(--color-primary)]',
          step.status === 'error' && 'border-destructive/60',
        )}
      >
        <button
          type="button"
          className="flex w-full flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-left font-mono text-xs disabled:cursor-default"
          aria-expanded={expandable ? open : undefined}
          aria-controls={expandable && open ? id : undefined}
          disabled={!expandable}
          onClick={() => setOpen((o) => !o)}
        >
          <ChevronRight
            aria-hidden
            className={cn(
              'size-3 text-muted-foreground transition-transform',
              open && 'rotate-90',
              !expandable && 'invisible',
            )}
          />
          <span className="text-[13px] font-semibold">{step.node}</span>
          <span className="text-muted-foreground">
            {step.status === 'paused' ? 'waiting for you' : tools}
          </span>
          {step.strong && (
            <Badge variant="strong" className="font-sans">
              STRONG TIER
            </Badge>
          )}
          <span className="flex flex-wrap items-center gap-3 text-muted-foreground max-sm:w-full sm:ml-auto">
            {flagged && (
              <Badge variant={flagged.tone} className="font-sans">
                {flagged.label}
              </Badge>
            )}
            {step.status === 'running' ? (
              <span>running…</span>
            ) : step.status === 'paused' ? (
              <span>—</span>
            ) : (
              <>
                <span>{t.model ? shortModel(t.model) : 'no LLM'}</span>
                {t.tokens > 0 && <span>{int(t.tokens)} tok</span>}
                <span>{ms(t.latencyMs)}</span>
                <span>{usd(t.cost)}</span>
              </>
            )}
          </span>
        </button>
        {open && expandable && (
          <div id={id} className="border-t">
            {step.error && <p className="px-3 py-2 text-sm text-danger-foreground">{step.error}</p>}
            {step.calls.map((call) => (
              <CallDetail key={call.seq} call={call} />
            ))}
          </div>
        )}
      </div>
    </li>
  )
}

function StepDot({ step, flagged }: { step: Step; flagged: boolean }) {
  const base =
    'absolute left-[3px] top-[9px] grid size-[17px] place-items-center rounded-full border-2 bg-background'
  if (step.status === 'running')
    return (
      <span className={cn(base, 'border-primary text-primary')}>
        <Loader2 className="size-2.5 animate-spin" aria-label="running" />
      </span>
    )
  if (step.status === 'paused')
    return (
      <span className={cn(base, 'border-primary text-primary ring-4 ring-primary/15')}>
        <Pause className="size-2.5" aria-label="paused" />
      </span>
    )
  if (step.status === 'error')
    return (
      <span className={cn(base, 'border-destructive text-danger-foreground')}>
        <X className="size-2.5" aria-label="failed" />
      </span>
    )
  if (step.strong)
    return (
      <span className={cn(base, 'border-warning text-warning-foreground')}>
        <Triangle className="size-2" aria-label="escalated" />
      </span>
    )
  if (flagged)
    return (
      <span className={cn(base, 'border-warning text-[9px] font-bold text-warning-foreground')}>
        <span aria-label="flagged">!</span>
      </span>
    )
  return (
    <span className={cn(base, 'border-success text-success-foreground')}>
      <Check className="size-2.5" aria-label="done" />
    </span>
  )
}

/** A badge for steps whose result needs attention: validation issues, PO match status. */
function stepFlag(step: Step): { label: string; tone: 'warning' | 'danger' | 'success' } | null {
  for (const c of step.calls) {
    const p = c.payload as Record<string, unknown> | null
    if (!p || typeof p !== 'object') continue
    if (c.tool === 'validate_record' && Array.isArray(p.issues) && p.issues.length > 0) {
      const n = p.issues.length
      return { label: `${n} issue${n > 1 ? 's' : ''}`, tone: 'warning' }
    }
    if (c.tool === 'match_invoice_to_po' && typeof p.status === 'string') {
      const status = p.status
      return {
        label: status.replace(/_/g, ' '),
        tone: status === 'matched' ? 'success' : status === 'partial' ? 'warning' : 'danger',
      }
    }
  }
  return null
}

function CallDetail({ call }: { call: TraceEvent }) {
  const strong = isStrongCall(call)
  const server = call.tool ? SERVER[call.tool] : undefined
  return (
    <div className="border-t border-dashed px-3 py-2.5 first:border-t-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-xs font-semibold">{call.tool}</span>
        {server && <Badge>{server} server</Badge>}
        {call.model && (
          <Badge variant={strong ? 'strong' : 'po'}>{strong ? 'strong tier' : 'default tier'}</Badge>
        )}
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-0.5 text-xs sm:grid-cols-[auto_1fr_auto_1fr]">
        <dt className="text-muted-foreground">model</dt>
        <dd className="font-mono">{call.model ?? '— (no LLM)'}</dd>
        <dt className="text-muted-foreground">tokens</dt>
        <dd className="font-mono">
          {call.tokens_in != null
            ? `${int(call.tokens_in)} in / ${int(call.tokens_out)} out`
            : '—'}
        </dd>
        <dt className="text-muted-foreground">latency</dt>
        <dd className="font-mono">{ms(call.latency_ms)}</dd>
        <dt className="text-muted-foreground">cost</dt>
        <dd className="font-mono">{usd(call.cost_usd)}</dd>
      </dl>
      <div className="mt-2 text-[10px] uppercase tracking-wider text-muted-foreground">Args</div>
      {call.args == null ? (
        <p className="mt-1 text-xs text-muted-foreground">not recorded</p>
      ) : (
        <JsonBlock value={call.args} className="mt-1" />
      )}
      <div className="mt-2 text-[10px] uppercase tracking-wider text-muted-foreground">Result</div>
      <JsonBlock value={call.payload} className="mt-1" />
    </div>
  )
}
