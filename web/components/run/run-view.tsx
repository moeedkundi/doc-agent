'use client'

import { ArrowDown, ArrowLeft, CheckCircle2, Loader2, Pause, Triangle, XCircle } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Notice, RateLimitNotice, ReplayBanner } from '@/components/notices'
import { DocumentViewer } from '@/components/run/document-viewer'
import { MatchDiff } from '@/components/run/match-diff'
import { PoNextStep, ScrollHint } from '@/components/run/next-steps'
import { ReviewForm } from '@/components/run/review-form'
import { Timeline } from '@/components/run/timeline'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { track } from '@/lib/analytics'
import { ApiError, api } from '@/lib/api'
import { docTypeLabel, int, usd } from '@/lib/format'
import { buildSteps, confidences, runTotals } from '@/lib/trace'
import type { PlainRecord, RunStatus, TraceEvent } from '@/lib/types'
import { useRun } from '@/lib/use-run'

export function RunView({
  id,
  fallback,
  documentHint,
}: {
  id: string
  fallback: string | null
  /** The document id from the URL: lets the page image load before the run does (LCP). */
  documentHint: string | null
}) {
  const state = useRun(id)
  const { run, events, status, review, result, errorCode, connection, loadError } = state
  const steps = useMemo(() => buildSteps(events), [events])
  const totals = useMemo(() => runTotals(steps), [steps])
  const conf = useMemo(() => confidences(events), [events])
  const escalated = steps.some((s) => s.strong) || result?.escalated === true
  const purchaseOrder = useMemo(() => poFromTrace(events), [events])
  const elapsed = useElapsed(events, status)
  useRunFinishedEvent(status, status !== 'completed' || !!result, {
    doc_type: result?.doc_type,
    match_status: result?.match?.status,
    escalated: result?.escalated,
    replay: run?.replay,
  })

  if (loadError) {
    return (
      <div className="container py-10">
        <Notice tone="danger" title={loadError.status === 404 ? 'Run not found' : "Couldn't load this run"}>
          {loadError.status === 404
            ? 'Runs belong to the browser session that started them, and sessions expire after a day.'
            : loadError.message}
        </Notice>
        <BackLink />
      </div>
    )
  }
  const documentId = run?.document_id ?? documentHint

  const docType = result?.doc_type ?? review?.doc_type ?? docTypeFromTrace(events)
  const record = result?.record
  const shown = record ?? review?.record
  return (
    <>
      {(run?.replay || fallback) && <ReplayBanner reason={fallback} />}
      <div className="border-b py-3">
        <div className="container flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <BackLink inline />
            <span className="text-muted-foreground" aria-hidden>/</span>
            <h1 className="font-semibold">{shown?.invoice_number ?? shown?.po_number ?? 'Run'}</h1>
            {docType && docType !== 'unknown' && (
              <Badge variant={docType === 'invoice' ? 'invoice' : 'po'}>{docTypeLabel(docType)}</Badge>
            )}
            {run ? (
              <StatusBadge status={status} />
            ) : (
              <span role="status" className="flex items-center gap-1 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" aria-hidden /> Loading run…
              </span>
            )}
            {escalated && (
              <Badge variant="strong">
                <Triangle /> Escalated to strong tier
              </Badge>
            )}
            {result?.match && result.match.status !== 'matched' && (
              <Badge variant={result.match.status === 'partial' ? 'warning' : 'danger'}>
                PO {result.match.status.replace(/_/g, ' ')}
              </Badge>
            )}
          </div>
          <div className="flex gap-1.5 max-sm:w-full" aria-label="Run totals">
            <Meter label={run?.replay ? 'Recorded cost' : 'Cost'} value={usd(totals.cost)} />
            <Meter label="Tokens" value={int(totals.tokens)} />
            <Meter label="Time" value={elapsed} />
          </div>
        </div>
      </div>

      <div className="container pb-10">
        <div className="grid gap-5 pt-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
          <div className="min-w-0">
            {documentId && <DocumentViewer documentId={documentId} />}
          </div>
          <div className="min-w-0 space-y-4">
            {connection === 'lost' && (
              <Notice tone="warning" title="Live updates stopped">
                The connection to the trace stream dropped. Reload the page to pick up where it
                left off.
              </Notice>
            )}
            {review && status === 'awaiting_review' && (
              <ReviewForm key={review.run_id} review={review} confidence={conf} onSubmit={state.submitReview} />
            )}
            <section aria-labelledby="trace-title">
              <div className="mb-2.5 flex items-center justify-between gap-2">
                <h2 id="trace-title" className="text-lg font-semibold">
                  Trace
                </h2>
                <span className="text-xs text-muted-foreground" aria-live="polite">
                  {steps.length} steps · {totals.toolCalls} tool calls ·{' '}
                  {status === 'awaiting_review'
                    ? 'paused · resumes on approve'
                    : connection === 'closed'
                      ? 'finished'
                      : connection === 'reconnecting'
                        ? 'reconnecting…'
                        : 'live via SSE'}
                </span>
              </div>
              {steps.length === 0 ? (
                <div className="flex items-center gap-2 rounded-lg border border-dashed px-3 py-4 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Waiting for the first step…
                </div>
              ) : (
                <Timeline steps={steps} />
              )}
            </section>
            <Outcome
              status={status}
              errorCode={errorCode}
              message={run?.error?.message ?? null}
              summary={result?.summary}
              documentId={run?.document_id ?? null}
              replayable={run?.error?.replay_available ?? false}
              docType={docType}
              poNumber={shown?.po_number ?? null}
              hasMatch={!!result?.match && docType === 'invoice'}
            />
          </div>
        </div>

        {result?.match && record && docType === 'invoice' && (
          <>
            <MatchDiff match={result.match} invoice={record} po={purchaseOrder} />
            <ScrollHint targetId="match-title" label="PO match results below" />
          </>
        )}
      </div>
    </>
  )
}

function BackLink({ inline = false }: { inline?: boolean }) {
  return (
    <Link
      href="/"
      className={inline ? 'flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground' : 'mt-4 inline-flex items-center gap-1 text-sm text-primary'}
    >
      <ArrowLeft className="size-3.5" aria-hidden /> Samples
    </Link>
  )
}

function Meter({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[92px] flex-1 rounded-lg border px-2.5 py-1 text-right">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="font-mono text-sm font-medium tabular-nums">{value}</div>
    </div>
  )
}

function StatusBadge({ status }: { status: RunStatus | null }) {
  switch (status) {
    case 'completed':
      return (
        <Badge variant="success">
          <CheckCircle2 /> Completed
        </Badge>
      )
    case 'awaiting_review':
      return (
        <Badge variant="primary">
          <Pause /> Awaiting your review
        </Badge>
      )
    case 'failed':
      return (
        <Badge variant="danger">
          <XCircle /> Failed
        </Badge>
      )
    case 'rejected':
      return <Badge variant="warning">Not a PO or invoice</Badge>
    default:
      return (
        <Badge>
          <Loader2 className="animate-spin" /> Running
        </Badge>
      )
  }
}

function Outcome({
  status,
  errorCode,
  message,
  summary,
  documentId,
  replayable,
  docType,
  poNumber,
  hasMatch,
}: {
  status: RunStatus | null
  errorCode: string | null
  message: string | null
  summary: string | undefined
  documentId: string | null
  replayable: boolean
  docType: string | null
  poNumber: string | null
  hasMatch: boolean
}) {
  const router = useRouter()
  const [retryAt, setRetryAt] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const watchRecording = async () => {
    if (!documentId) return
    try {
      const r = await api.startRun(documentId, true)
      router.push(`/runs/${r.run_id}?fallback=${errorCode ?? 'budget_exceeded'}&doc=${documentId}`)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'rate_limited') setRetryAt(Date.now() + (e.retryAfterSeconds ?? 60) * 1000)
      else setError(e instanceof ApiError && e.code === 'no_recording' ? 'Only the sample documents have recordings.' : String(e instanceof Error ? e.message : e))
    }
  }

  if (status === 'completed' && summary) {
    return (
      <div className="space-y-3">
        <Notice tone="info" title="Done">
          {summary}
          {hasMatch && (
            <div className="mt-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  document.getElementById('match-title')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }
              >
                See the PO match below <ArrowDown />
              </Button>
            </div>
          )}
        </Notice>
        {docType === 'purchase_order' && <PoNextStep poNumber={poNumber} />}
      </div>
    )
  }
  if (status === 'rejected') {
    return (
      <Notice tone="warning" title="Not a purchase order or invoice">
        {summary ?? 'The classifier did not recognise this document, so nothing was extracted or saved.'}
      </Notice>
    )
  }
  if (status !== 'failed') return null
  return (
    <div className="space-y-2">
      <Notice tone="danger" title={errorCode === 'budget_exceeded' ? 'The LLM budget ran out mid-run' : 'The run failed'}>
        {message ?? 'Something went wrong.'}
        {replayable && documentId && (
          <div className="mt-2">
            <Button size="sm" variant="outline" onClick={watchRecording} disabled={retryAt !== null}>
              Watch the recorded run instead
            </Button>
          </div>
        )}
      </Notice>
      {retryAt !== null && <RateLimitNotice until={retryAt} onDone={() => setRetryAt(null)} />}
      {error && <Notice tone="danger" title="No recording">{error}</Notice>}
    </div>
  )
}

const TERMINAL: RunStatus[] = ['completed', 'failed', 'rejected']

/** One `run_finished` event when a run the visitor watched live ends (not when an old run
 * page is reopened: its first status is already final). */
function useRunFinishedEvent(
  status: RunStatus | null,
  ready: boolean,
  details: { doc_type?: string; match_status?: string; escalated?: boolean; replay?: boolean },
) {
  const watchedLive = useRef(false)
  const sent = useRef(false)
  useEffect(() => {
    if (!status) return
    if (!TERMINAL.includes(status)) {
      watchedLive.current = true
      return
    }
    if (watchedLive.current && ready && !sent.current) {
      sent.current = true
      track('run_finished', { status, ...details })
    }
  }, [status, ready, details])
}

function useElapsed(events: TraceEvent[], status: RunStatus | null): string {
  const [now, setNow] = useState(() => Date.now())
  const live = status === 'running' || status === 'queued'
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [live])
  const first = events[0]
  const last = events.at(-1)
  if (!first || !last) return '—'
  const end = live ? Math.max(now, Date.parse(last.ts)) : Date.parse(last.ts)
  return `${Math.max(0, (end - Date.parse(first.ts)) / 1000).toFixed(1)} s`
}

function poFromTrace(events: TraceEvent[]): PlainRecord | null {
  for (const e of events) {
    if (e.tool !== 'get_purchase_order' || e.kind !== 'tool_end') continue
    const p = e.payload as { found?: boolean; purchase_order?: PlainRecord } | null
    if (p?.found && p.purchase_order) return p.purchase_order
  }
  return null
}

function docTypeFromTrace(events: TraceEvent[]): string | null {
  for (const e of events) {
    if (e.tool === 'classify_document' && e.kind === 'tool_end') {
      const p = e.payload as { doc_type?: string } | null
      if (p?.doc_type) return p.doc_type
    }
  }
  return null
}
