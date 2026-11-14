'use client'

import { AlertTriangle, Loader2 } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Notice } from '@/components/notices'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ApiError } from '@/lib/api'
import type { PlainRecord, ReviewPayload, ValidationIssue } from '@/lib/types'
import { cn } from '@/lib/utils'

interface FieldDef {
  path: string
  label: string
}

const HEADER: Record<string, FieldDef[]> = {
  invoice: [
    { path: 'invoice_number', label: 'Invoice number' },
    { path: 'po_number', label: 'PO number' },
    { path: 'invoice_date', label: 'Invoice date' },
    { path: 'due_date', label: 'Due date' },
    { path: 'seller.name', label: 'Seller' },
    { path: 'seller.tax_id', label: 'Seller tax ID' },
    { path: 'seller.address', label: 'Seller address' },
    { path: 'buyer.name', label: 'Buyer' },
    { path: 'buyer.address', label: 'Buyer address' },
    { path: 'currency', label: 'Currency' },
  ],
  purchase_order: [
    { path: 'po_number', label: 'PO number' },
    { path: 'po_date', label: 'PO date' },
    { path: 'vendor.name', label: 'Vendor' },
    { path: 'vendor.tax_id', label: 'Vendor tax ID' },
    { path: 'vendor.address', label: 'Vendor address' },
    { path: 'buyer.name', label: 'Buyer' },
    { path: 'buyer.address', label: 'Buyer address' },
    { path: 'ship_to', label: 'Ship to' },
    { path: 'requested_delivery_date', label: 'Requested delivery' },
    { path: 'payment_terms', label: 'Payment terms' },
    { path: 'currency', label: 'Currency' },
  ],
}
const TOTALS: FieldDef[] = [
  { path: 'subtotal', label: 'Subtotal' },
  { path: 'tax_amount', label: 'Tax' },
  { path: 'total', label: 'Total' },
]
const LINE_COLS = [
  { key: 'sku', label: 'SKU', numeric: false },
  { key: 'description', label: 'Description', numeric: false },
  { key: 'quantity', label: 'Qty', numeric: true },
  { key: 'unit_price', label: 'Unit price', numeric: true },
  { key: 'line_total', label: 'Total', numeric: true },
] as const

function getAt(record: unknown, path: string): unknown {
  let cur: unknown = record
  for (const seg of path.split('.')) {
    const m = /^(\w+)(?:\[(\d+)\])?$/.exec(seg)
    if (!m || cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[m[1]!]
    if (m[2] !== undefined) cur = Array.isArray(cur) ? cur[Number(m[2])] : undefined
  }
  return cur
}

function setAt(record: PlainRecord, path: string, value: string | null): PlainRecord {
  const next = structuredClone(record)
  const segs = path.split('.')
  let cur: Record<string, unknown> = next
  segs.forEach((seg, i) => {
    const m = /^(\w+)(?:\[(\d+)\])?$/.exec(seg)!
    const key = m[1]!
    const last = i === segs.length - 1
    if (m[2] !== undefined) {
      const arr = cur[key] as Record<string, unknown>[]
      if (last) (arr as unknown[])[Number(m[2])] = value
      else cur = arr[Number(m[2])]!
    } else if (last) {
      cur[key] = value
    } else {
      cur[key] ??= {}
      cur = cur[key] as Record<string, unknown>
    }
  })
  return next
}

/** "line_items[1].unit_price" -> "Line 2, unit price" (lines are numbered from 1). */
export function fieldLabel(path: string): string {
  const m = /^line_items\[(\d+)\]\.(\w+)$/.exec(path)
  if (m) return `Line ${Number(m[1]) + 1}, ${m[2]!.replace(/_/g, ' ')}`
  return path.replace(/_/g, ' ').replace(/\./g, ' › ')
}

export function ReviewForm({
  review,
  confidence,
  onSubmit,
}: {
  review: ReviewPayload
  confidence: Map<string, number>
  onSubmit: (decision: { action: 'approve' | 'edit'; record?: unknown }) => Promise<void>
}) {
  const [record, setRecord] = useState<PlainRecord>(review.record)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const issuesByPath = useMemo(() => {
    const map = new Map<string, ValidationIssue[]>()
    for (const issue of review.issues) {
      map.set(issue.field_path, [...(map.get(issue.field_path) ?? []), issue])
    }
    return map
  }, [review.issues])
  const recordLevel = issuesByPath.get('') ?? []
  const edited = JSON.stringify(record) !== JSON.stringify(review.record)
  const header = HEADER[review.doc_type] ?? HEADER.invoice!
  const firstFlagged = review.issues.find((i) => i.field_path)?.field_path

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await onSubmit(edited ? { action: 'edit', record } : { action: 'approve' })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      setBusy(false)
    }
  }

  const reasons = (id: string, issues: ValidationIssue[]) => (
    <div id={`${id}-why`} className="mt-1 space-y-0.5 text-xs text-warning-foreground">
      {issues.map((i) => (
        <p key={i.rule} className="flex gap-1">
          <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
          <span>
            <span className="break-all font-mono">{i.rule}</span> — {i.message}
          </span>
        </p>
      ))}
    </div>
  )
  const idOf = (path: string) => `f-${path.replace(/[^\w]/g, '-')}`

  const input = (
    path: string,
    label: string,
    opts: { numeric?: boolean; compact?: boolean; reasonsBelow?: boolean } = {},
  ) => {
    const issues = issuesByPath.get(path) ?? []
    const flagged = issues.length > 0
    const conf = confidence.get(path)
    const id = idOf(path)
    const value = getAt(record, path)
    return (
      <div key={path} className={cn(opts.compact && 'min-w-0')}>
        {!opts.compact && (
          <label htmlFor={id} className="mb-1 flex justify-between gap-2 text-xs font-medium">
            <span>
              {label}
              {flagged && <span className="sr-only"> (flagged)</span>}
            </span>
            {conf !== undefined && (
              <span className={cn('font-mono text-[11px]', conf < 0.8 ? 'text-warning-foreground' : 'text-muted-foreground')}>
                conf {conf.toFixed(2)}
              </span>
            )}
          </label>
        )}
        <input
          id={id}
          aria-label={opts.compact ? label : undefined}
          aria-invalid={flagged || undefined}
          aria-describedby={flagged ? `${id}-why` : undefined}
          autoFocus={path === firstFlagged}
          inputMode={opts.numeric ? 'decimal' : undefined}
          value={value == null ? '' : String(value)}
          onChange={(e) => setRecord((r) => setAt(r, path, e.target.value === '' ? null : e.target.value))}
          className={cn(
            'h-9 w-full rounded-md border border-input bg-background px-2.5 font-mono text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
            opts.numeric && 'text-right',
            flagged && 'border-warning ring-[3px] ring-warning/25',
          )}
        />
        {flagged && !opts.reasonsBelow && reasons(id, issues)}
      </div>
    )
  }

  return (
    <Card className="border-primary/60 p-4" aria-labelledby="review-title">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="review-title" className="text-lg font-semibold">
            Review needed
          </h2>
          <span className="text-xs text-muted-foreground">
            {review.issues.length} field{review.issues.length === 1 ? '' : 's'} flagged · edit
            anything, then approve
          </span>
        </div>
        {review.notes.length > 0 && (
          <ul className="mt-1 text-[13px] text-muted-foreground">
            {review.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
        {recordLevel.length > 0 && (
          <div className="mt-3 space-y-1.5">
            {recordLevel.map((i) => (
              <Notice key={i.rule} tone="warning" title={i.rule}>
                {i.message}
              </Notice>
            ))}
          </div>
        )}

        <div className="mt-3 grid gap-3 sm:grid-cols-2">{header.map((f) => input(f.path, f.label))}</div>

        <h3 className="mt-4 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          Line items
        </h3>
        <div className="mt-1 overflow-x-auto">
          <table className="w-full min-w-[520px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b text-left text-[11px] text-muted-foreground">
                <th scope="col" className="w-7 px-1 py-1 font-medium">#</th>
                {LINE_COLS.map((c) => (
                  <th key={c.key} scope="col" className={cn('px-1 py-1 font-medium', c.numeric && 'text-right')}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {record.line_items.map((_, i) => {
                const lineIssues = LINE_COLS.flatMap((c) => issuesByPath.get(`line_items[${i}].${c.key}`) ?? [])
                return (
                  <tr key={i} className={cn('border-b align-top', lineIssues.length > 0 && 'bg-warning-soft/50')}>
                    <td className="px-1 py-1.5 font-mono text-xs text-muted-foreground">{i + 1}</td>
                    {LINE_COLS.map((c) => (
                      <td key={c.key} className={cn('px-1 py-1', c.key === 'description' && 'min-w-[160px]')}>
                        {input(`line_items[${i}].${c.key}`, `Line ${i + 1} ${c.label}`, {
                          numeric: c.numeric,
                          compact: true,
                        })}
                      </td>
                    ))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <div className="mt-3 grid gap-2.5 sm:grid-cols-3">
          {TOTALS.map((f) =>
            input(f.path, f.path === 'total' && record.currency ? `Total (${record.currency})` : f.label, {
              numeric: true,
              reasonsBelow: true,
            }),
          )}
        </div>
        {TOTALS.map((f) => {
          const issues = issuesByPath.get(f.path) ?? []
          return issues.length > 0 ? <div key={f.path}>{reasons(idOf(f.path), issues)}</div> : null
        })}

        {error && (
          <Notice tone="danger" title="Couldn't submit the review" className="mt-3">
            {error}
          </Notice>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={!edited || busy} onClick={() => setRecord(review.record)}>
            Reset edits
          </Button>
          <Button type="submit" disabled={busy}>
            {busy && <Loader2 className="animate-spin" />}
            {edited ? 'Save edits & continue' : 'Approve & continue'}
          </Button>
        </div>
      </form>
    </Card>
  )
}
