import { AlertTriangle } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { amount } from '@/lib/format'
import type { LineItem, MatchResult, PlainRecord } from '@/lib/types'
import { cn } from '@/lib/utils'

const STATUS: Record<string, { label: string; tone: 'success' | 'warning' | 'danger' }> = {
  matched: { label: 'Matched', tone: 'success' },
  partial: { label: 'Partial delivery', tone: 'warning' },
  mismatch: { label: 'Mismatch', tone: 'danger' },
  po_not_found: { label: 'PO not found', tone: 'danger' },
}

/** Which numbers on a matched pair disagree, so they can be highlighted. */
function differences(po: LineItem | undefined, inv: LineItem | undefined) {
  const differs = (k: keyof LineItem) =>
    po?.[k] != null && inv?.[k] != null && Number(po[k]) !== Number(inv[k])
  return { qty: differs('quantity'), price: differs('unit_price'), total: differs('line_total') }
}

interface Row {
  po: number | null
  inv: number | null
  method?: string
  score?: number
}

/** Side-by-side PO vs invoice. Lines are numbered from 1 (the API's indexes are 0-based). */
export function MatchDiff({
  match,
  invoice,
  po,
}: {
  match: MatchResult
  invoice: PlainRecord
  po: PlainRecord | null
}) {
  const status = STATUS[match.status] ?? STATUS.mismatch!
  const flaggedInv = new Set(match.issues.map((i) => i.invoice_line).filter((x) => x != null))
  const flaggedPo = new Set(match.issues.map((i) => i.po_line).filter((x) => x != null))
  const totalIssue = match.issues.some((i) => i.invoice_line == null && /total/.test(i.rule))

  const rows: Row[] = match.line_matches.map((m) => ({
    po: m.po_line,
    inv: m.invoice_line,
    method: m.method,
    score: m.score,
  }))
  const pairedInv = new Set(rows.map((r) => r.inv))
  const pairedPo = new Set(rows.map((r) => r.po))
  invoice.line_items.forEach((_, i) => !pairedInv.has(i) && rows.push({ po: null, inv: i }))
  po?.line_items.forEach((_, i) => !pairedPo.has(i) && rows.push({ po: i, inv: null }))
  rows.sort((a, b) => (a.inv ?? a.po ?? 0) - (b.inv ?? b.po ?? 0))

  const poNumber = match.po_number ?? invoice.po_number ?? '—'
  return (
    <section aria-labelledby="match-title" className="mt-6 scroll-mt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="match-title" className="text-lg font-semibold">
          PO match · <span className="font-mono text-[15px]">{poNumber}</span> vs{' '}
          <span className="font-mono text-[15px]">{invoice.invoice_number}</span>
        </h2>
        <Badge variant={status.tone}>
          {status.label}
          {match.issues.length > 0 &&
            ` · ${match.issues.length} issue${match.issues.length > 1 ? 's' : ''}`}
        </Badge>
      </div>

      {match.issues.map((issue, i) => (
        <div
          key={i}
          className="mt-2 flex gap-2.5 rounded-md border border-destructive/40 bg-danger-soft px-3 py-2 text-[13px]"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-danger-foreground" aria-hidden />
          <div>
            <span className="font-mono text-xs font-semibold">{issue.rule}</span> ·{' '}
            {issue.message}
          </div>
        </div>
      ))}

      {po === null ? (
        <p className="mt-3 text-sm text-muted-foreground">
          No purchase order {poNumber} in this session, so there is nothing to compare line by
          line. Process the matching PO sample first.
        </p>
      ) : (
        <div className="mt-3 overflow-hidden rounded-lg border">
          <div className="grid md:grid-cols-2">
            <div className="flex justify-between bg-secondary px-3 py-2 text-[13px] font-semibold max-md:hidden">
              <span>Purchase order {po.po_number}</span>
              <span className="font-mono text-muted-foreground">{po.currency}</span>
            </div>
            <div className="flex justify-between border-l bg-secondary px-3 py-2 text-[13px] font-semibold max-md:hidden">
              <span>Invoice {invoice.invoice_number}</span>
              <span className="font-mono text-muted-foreground">{invoice.currency}</span>
            </div>
            {rows.map((row) => {
              const p = row.po != null ? po.line_items[row.po] : undefined
              const v = row.inv != null ? invoice.line_items[row.inv] : undefined
              const d = differences(p, v)
              // Red: a flagged pair or an invoiced line the PO doesn't have. Amber: a PO line
              // not invoiced yet (normal for a partial delivery).
              const bad =
                (row.inv != null && flaggedInv.has(row.inv)) ||
                (row.po != null && flaggedPo.has(row.po)) ||
                row.po == null
              const pending = !bad && row.inv == null
              const key = `${row.po ?? 'x'}-${row.inv ?? 'x'}`
              return [
                <LineCell key={`${key}-po`} side="PO" index={row.po} line={p} bad={bad} pending={pending} diff={d} />,
                <LineCell
                  key={`${key}-inv`}
                  side="INV"
                  index={row.inv}
                  line={v}
                  bad={bad}
                  pending={pending}
                  diff={d}
                  note={row.method ? `matched by ${row.method} · ${Math.round(row.score ?? 0)}` : undefined}
                  className="md:border-l"
                />,
              ]
            })}
            <TotalCell label={<><span className="md:hidden">PO </span>Total incl. tax</>} value={po.total} />
            <TotalCell
              label={<><span className="md:hidden">Invoice </span>Total incl. tax</>}
              value={invoice.total}
              bad={totalIssue}
              className="md:border-l"
            />
          </div>
        </div>
      )}
    </section>
  )
}

function LineCell({
  side,
  index,
  line,
  bad,
  pending,
  diff,
  note,
  className,
}: {
  side: 'PO' | 'INV'
  index: number | null
  line: LineItem | undefined
  bad: boolean
  pending: boolean
  diff: { qty: boolean; price: boolean; total: boolean }
  note?: string
  className?: string
}) {
  const mark = (on: boolean, text: string) =>
    on ? <mark className="bg-transparent font-semibold text-danger-foreground">{text}</mark> : text
  return (
    <div
      className={cn(
        'grid grid-cols-[22px_1fr_auto] gap-2 border-t px-3 py-2.5 text-[13px]',
        bad && 'bg-danger-soft',
        pending && 'bg-warning-soft/60',
        className,
      )}
    >
      <span className="font-mono text-xs text-muted-foreground">{index != null ? index + 1 : ''}</span>
      {line ? (
        <>
          <div className="min-w-0">
            <Badge variant={side === 'PO' ? 'po' : 'invoice'} className="mr-1.5 md:hidden">
              {side}
            </Badge>
            {line.description}
            <div className="text-xs text-muted-foreground">
              {line.sku && <span className="font-mono">{line.sku}</span>}
              {line.sku && note && ' · '}
              {note}
            </div>
          </div>
          <div className="whitespace-nowrap text-right font-mono text-xs">
            {mark(diff.qty, amount(line.quantity).replace(/\.00$/, ''))} ×{' '}
            {mark(diff.price, amount(line.unit_price))}
            <br />
            {mark(diff.total, amount(line.line_total))}
          </div>
        </>
      ) : (
        <div className={cn('col-span-2 text-xs', pending ? 'text-warning-foreground' : 'text-danger-foreground')}>
          <Badge variant={side === 'PO' ? 'po' : 'invoice'} className="mr-1.5 md:hidden">
            {side}
          </Badge>
          {side === 'PO' ? 'Not on the PO' : 'Not invoiced yet'}
        </div>
      )}
    </div>
  )
}

function TotalCell({
  label,
  value,
  bad = false,
  className,
}: {
  label: React.ReactNode
  value: string | null | undefined
  bad?: boolean
  className?: string
}) {
  return (
    <div
      className={cn(
        'grid grid-cols-[22px_1fr_auto] gap-2 border-t px-3 py-2.5 text-[13px] font-semibold',
        bad && 'bg-danger-soft',
        className,
      )}
    >
      <span />
      <span>{label}</span>
      <span className={cn('font-mono text-xs', bad && 'text-danger-foreground')}>{amount(value)}</span>
    </div>
  )
}
