'use client'

import { ExternalLink, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Notice } from '@/components/notices'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { ApiError, api } from '@/lib/api'
import { usd } from '@/lib/format'
import { REPO_URL, repoFile } from '@/lib/site'
import type { EvalReport } from '@/lib/types'
import { cn } from '@/lib/utils'

const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? '—' : `${(v * 100).toFixed(digits)}%`

export function EvalsView() {
  const [report, setReport] = useState<EvalReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.evals().then(setReport, (e: unknown) =>
      setError(e instanceof ApiError && e.status === 404 ? 'No eval report has been published yet.' : String(e instanceof Error ? e.message : e)),
    )
  }, [])

  if (error) return <Notice tone="danger" title="Couldn't load the eval report" className="mt-6">{error}</Notice>
  if (!report)
    return (
      <p className="mt-8 flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <Loader2 className="size-4 animate-spin" /> Loading the latest report…
      </p>
    )

  const m = report.metrics
  const date = new Date(report.finished_at)
  const scannedDocs = m.clean_vs_scanned.scanned.documents
  return (
    <>
      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span>
          Last run{' '}
          <time dateTime={report.finished_at} className="font-medium text-foreground">
            {date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })}
          </time>{' '}
          · {report.suite} suite · {m.documents} documents
        </span>
        {report.ci_run_url ? (
          <a href={report.ci_run_url} className="inline-flex items-center gap-1 text-primary hover:underline dark:text-violet-300">
            CI run <ExternalLink className="size-3.5" aria-hidden />
          </a>
        ) : (
          <span>
            run locally ·{' '}
            <a href={`${REPO_URL}/actions/workflows/eval.yml`} className="inline-flex items-center gap-1 text-primary hover:underline dark:text-violet-300">
              eval workflow on GitHub <ExternalLink className="size-3.5" aria-hidden />
            </a>
          </span>
        )}
        <a href={repoFile('evals/README.md')} className="inline-flex items-center gap-1 text-primary hover:underline dark:text-violet-300">
          how fields are scored <ExternalLink className="size-3.5" aria-hidden />
        </a>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Headline label="Field accuracy" value={pct(m.field_accuracy)} note={`${m.failed_runs} failed runs`} />
        <Headline label="Mismatch recall" value={pct(m.mismatch_recall, 0)} note="PO mismatches caught" />
        <Headline label="Cost per document" value={usd(m.cost_per_doc_usd.avg, 4)} note={`max ${usd(m.cost_per_doc_usd.max, 4)}`} />
        <Headline label="p95 latency" value={m.latency_s.p95 == null ? '—' : `${m.latency_s.p95.toFixed(1)} s`} note={`p50 ${m.latency_s.p50?.toFixed(1) ?? '—'} s`} />
      </dl>

      <Notice tone="info" title="The dataset is synthetic" className="mt-5">
        {m.documents} generated documents for fictional office/IT suppliers ({m.by_doc_type.purchase_order?.documents ?? 0} POs,{' '}
        {m.by_doc_type.invoice?.documents ?? 0} invoices, {scannedDocs} scanned variants) in a handful of layout templates, with
        injected PO mismatches. High scores here mean the pipeline handles these layouts well, not that it will on real-world
        documents; more layout templates and noisier scans are planned.
      </Notice>

      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card className="p-4">
          <h2 className="font-semibold">Per-field accuracy</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Share of values that matched ground truth (line-item fields count every line). Fields below 100% come first.
          </p>
          <FieldChart perField={m.per_field_accuracy} />
        </Card>
        <div className="space-y-5">
          <Card className="p-4">
            <h2 className="font-semibold">Clean vs scanned</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Scanned variants have the same content as a clean twin, rasterised with skew, softening and grain.
            </p>
            <div className="mt-3 space-y-2.5">
              <Bar label={`Clean (${m.clean_vs_scanned.clean.documents})`} value={m.clean_vs_scanned.clean.field_accuracy} />
              <Bar label={`Scanned (${m.clean_vs_scanned.scanned.documents})`} value={m.clean_vs_scanned.scanned.field_accuracy} />
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              Paired ({m.clean_vs_scanned.paired.pairs} pairs): clean twins {pct(m.clean_vs_scanned.paired.clean_twins)} · scanned{' '}
              {pct(m.clean_vs_scanned.paired.scanned_variants)}
            </p>
          </Card>
          <Card className="p-4">
            <h2 className="font-semibold">Matching and routing</h2>
            <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 text-sm">
              <Row label="Classification accuracy" value={pct(m.classification_accuracy)} />
              <Row label="PO match status accuracy" value={pct(m.match_status_accuracy)} />
              {Object.entries(m.mismatch_recall_by_kind).map(([kind, v]) => (
                <Row key={kind} label={`Recall · ${kind.replace(/_/g, ' ')}`} value={pct(v, 0)} />
              ))}
              <Row label="Escalated to strong tier" value={pct(m.escalation_rate)} />
              <Row label="Sent to human review" value={pct(m.review_rate)} />
              <Row label="Total eval cost" value={usd(m.cost_per_doc_usd.total, 2)} />
            </dl>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {Object.entries(report.models).map(([tier, model]) => (
                <Badge key={tier} variant={tier === 'strong' ? 'warning' : 'po'} className="font-mono font-normal">
                  {tier}: {model}
                </Badge>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  )
}

function Headline({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <Card className="px-4 py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 font-mono text-2xl font-semibold tabular-nums">{value}</dd>
      <dd className="text-xs text-muted-foreground">{note}</dd>
    </Card>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-mono tabular-nums">{value}</dd>
    </>
  )
}

function Bar({ label, value }: { label: string; value: number | null }) {
  const v = value ?? 0
  return (
    <div>
      <div className="flex justify-between text-xs">
        <span>{label}</span>
        <span className="font-mono tabular-nums">{pct(value)}</span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-secondary" aria-hidden>
        <div className={cn('h-full rounded-full', v < 1 ? 'bg-warning' : 'bg-primary')} style={{ width: `${v * 100}%` }} />
      </div>
    </div>
  )
}

function FieldChart({ perField }: { perField: Record<string, number> }) {
  const entries = Object.entries(perField).sort(
    ([ka, a], [kb, b]) => (a < 1 ? 0 : 1) - (b < 1 ? 0 : 1) || a - b || ka.localeCompare(kb),
  )
  return (
    <table className="mt-3 w-full border-collapse text-xs">
      <caption className="sr-only">Per-field accuracy</caption>
      <thead className="sr-only">
        <tr>
          <th scope="col">Field</th>
          <th scope="col">Accuracy</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([key, v]) => {
          const [docType, ...rest] = key.split('.')
          return (
            <tr key={key}>
              <th scope="row" className="w-1/2 py-[3px] pr-3 text-left font-normal">
                <span className={cn('mr-1.5 inline-block w-7 rounded px-1 text-center text-[10px] font-semibold', docType === 'invoice' ? 'bg-primary/10 text-primary dark:text-violet-300' : 'bg-secondary')}>
                  {docType === 'invoice' ? 'INV' : 'PO'}
                </span>
                <span className="font-mono">{rest.join('.')}</span>
              </th>
              <td className="py-[3px]">
                <div className="flex items-center gap-2">
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary" aria-hidden>
                    <div className={cn('h-full rounded-full', v < 1 ? 'bg-warning' : 'bg-primary/70')} style={{ width: `${v * 100}%` }} />
                  </div>
                  <span className={cn('w-12 text-right font-mono tabular-nums', v < 1 && 'font-semibold text-warning-foreground')}>{pct(v)}</span>
                </div>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
