'use client'

import { ArrowUp, Ban, ChevronRight, Loader2, ShieldAlert } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Notice, RateLimitNotice } from '@/components/notices'
import { SqlBlock } from '@/components/sql-block'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ApiError, api } from '@/lib/api'
import { usd } from '@/lib/format'
import { readSse } from '@/lib/sse'
import type { AskResult, EndEvent, TraceEvent } from '@/lib/types'
import { cn } from '@/lib/utils'

const SUGGESTED = [
  'Which invoices don’t match their PO, and why?',
  'What is the total invoiced per vendor, by currency?',
  'Which purchase orders still have value left to invoice?',
  'List the five most expensive line items across all invoices.',
]
const WRITE_ATTEMPT = 'Delete every invoice from Brightledger Stationers.'

const STEP_LABEL: Record<string, string> = {
  ask_plan: 'Planning the query…',
  ask_run: 'Running the SELECT…',
  ask_sql: 'Fixing the SQL after an error…',
  ask_convert: 'Converting currency…',
  ask_answer: 'Writing the answer…',
}

interface Exchange {
  id: number
  question: string
  status: 'running' | 'done' | 'failed'
  step: string | null
  calls: number
  result: AskResult | null
  error: string | null
}

const STORE_KEY = 'docagent.ask'

/** The chat survives tab switches within the browser session (client-only component). */
function loadSaved(): Exchange[] {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE_KEY) ?? '[]') as Exchange[]
    return saved.filter((x) => x.status !== 'running')
  } catch {
    return []
  }
}

export function AskChat() {
  const [items, setItems] = useState<Exchange[]>(loadSaved)
  const [text, setText] = useState('')
  const [retryAt, setRetryAt] = useState<number | null>(null)
  const [blocked, setBlocked] = useState<string | null>(null)
  const nextId = useRef(items.reduce((m, x) => Math.max(m, x.id), 0) + 1)
  const bottom = useRef<HTMLDivElement>(null)
  const busy = items.some((i) => i.status === 'running')

  useEffect(() => {
    try {
      sessionStorage.setItem(STORE_KEY, JSON.stringify(items.filter((x) => x.status !== 'running')))
    } catch {
      // storage blocked or full: the chat just isn't kept across reloads
    }
  }, [items])

  const update = (id: number, patch: Partial<Exchange>) =>
    setItems((xs) => xs.map((x) => (x.id === id ? { ...x, ...patch } : x)))

  const ask = useCallback(async (question: string) => {
    const q = question.trim()
    if (!q) return
    const id = nextId.current++
    setText('')
    setBlocked(null)
    setItems((xs) => [
      ...xs,
      { id, question: q, status: 'running', step: 'ask_plan', calls: 0, result: null, error: null },
    ])
    requestAnimationFrame(() => bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }))
    try {
      const res = await api.ask(q)
      let calls = 0
      for await (const msg of readSse(res)) {
        if (msg.event === 'node_start') {
          const e = JSON.parse(msg.data) as TraceEvent
          update(id, { step: e.node })
        } else if (msg.event === 'tool_end') {
          update(id, { calls: ++calls })
        } else if (msg.event === 'end') {
          const end = JSON.parse(msg.data) as EndEvent
          const result = end.result as unknown as AskResult | null
          if (end.status === 'completed' && result) update(id, { status: 'done', result, step: null })
          else update(id, { status: 'failed', step: null, error: `The question failed (${end.error_code ?? end.status}).` })
        }
      }
      setItems((xs) =>
        xs.map((x) =>
          x.id === id && x.status === 'running'
            ? { ...x, status: 'failed', step: null, error: 'The stream ended without an answer.' }
            : x,
        ),
      )
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, 'unknown', String(e))
      setItems((xs) => xs.filter((x) => x.id !== id))
      setText(q)
      if (err.code === 'rate_limited') setRetryAt(Date.now() + (err.retryAfterSeconds ?? 60) * 1000)
      else setBlocked(err.message)
    }
  }, [])

  const disabled = busy || retryAt !== null
  return (
    <div className="mt-6">
      <div className="flex flex-wrap gap-2" aria-label="Suggested questions">
        {SUGGESTED.map((q) => (
          <Button key={q} variant="outline" size="sm" className="h-auto whitespace-normal py-1.5 text-left" disabled={disabled} onClick={() => ask(q)}>
            {q}
          </Button>
        ))}
        <Button
          variant="outline"
          size="sm"
          className="h-auto whitespace-normal border-destructive/50 py-1.5 text-left text-danger-foreground hover:bg-danger-soft hover:text-danger-foreground"
          disabled={disabled}
          onClick={() => ask(WRITE_ATTEMPT)}
        >
          <Ban /> Try a write: “{WRITE_ATTEMPT}”
        </Button>
      </div>

      <div className="mt-6 space-y-5" aria-live="polite">
        {items.length === 0 && (
          <p className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
            No questions yet. The data comes from documents you process on the{' '}
            <Link href="/" className="text-primary underline-offset-2 hover:underline dark:text-violet-300">
              Demo
            </Link>{' '}
            tab — run a PO and its invoice first, then ask about them.
          </p>
        )}
        {items.map((x) => (
          <ExchangeView key={x.id} x={x} />
        ))}
        <div ref={bottom} />
      </div>

      <div className="mt-5 space-y-2">
        {retryAt !== null && <RateLimitNotice until={retryAt} onDone={() => setRetryAt(null)} />}
        {blocked && <Notice tone="warning" title="Can't ask right now">{blocked}</Notice>}
      </div>
      <form
        className="mt-3 flex gap-2 rounded-lg border bg-background p-2 shadow-sm"
        onSubmit={(e) => {
          e.preventDefault()
          if (!disabled) void ask(text)
        }}
      >
        <label htmlFor="ask-input" className="sr-only">
          Your question
        </label>
        <input
          id="ask-input"
          value={text}
          maxLength={500}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask about your POs and invoices…"
          className="h-9 min-w-0 flex-1 bg-transparent px-2 text-sm outline-none placeholder:text-muted-foreground"
          autoComplete="off"
        />
        <Button type="submit" size="icon" disabled={disabled || !text.trim()} aria-label="Ask">
          {busy ? <Loader2 className="animate-spin" /> : <ArrowUp />}
        </Button>
      </form>
    </div>
  )
}

function ExchangeView({ x }: { x: Exchange }) {
  const r = x.result
  const refused = r?.plan.action === 'refuse'
  return (
    <div className="space-y-2">
      <div className="ml-auto w-fit max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
        {x.question}
      </div>
      <Card className={cn('p-3.5', refused && 'border-destructive/50')}>
        {x.status === 'running' ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            {STEP_LABEL[x.step ?? ''] ?? 'Working…'}
            {x.calls > 0 && <span className="font-mono text-xs">· {x.calls} calls</span>}
          </p>
        ) : x.status === 'failed' || !r ? (
          <p className="text-sm text-danger-foreground">{x.error ?? 'Something went wrong.'}</p>
        ) : (
          <>
            <div className="flex items-start gap-2">
              {refused && <ShieldAlert className="mt-0.5 size-4 shrink-0 text-danger-foreground" aria-hidden />}
              <p className="text-sm">{r.answer}</p>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              {refused ? (
                <Badge variant="danger">Refused · no SQL ran</Badge>
              ) : r.sql_error ? (
                <Badge variant="warning">SQL error: {r.sql_error.code}</Badge>
              ) : r.rows ? (
                <Badge variant="success">
                  {r.rows.length} row{r.rows.length === 1 ? '' : 's'}
                  {r.truncated ? ' (truncated)' : ''}
                </Badge>
              ) : null}
              <span className="font-mono">{usd(r.cost_usd)}</span>
            </div>
            {refused && (
              <p className="mt-2 text-xs text-muted-foreground">
                Writes are refused when the query is planned — and even a write that slipped
                through would be rejected by sqlstore: it only runs a single{' '}
                <code className="font-mono">SELECT</code>, as a read-only database role. Records
                are saved only by the typed <code className="font-mono">save_record</code> tool.
              </p>
            )}
            {r.sql && (
              <details className="group mt-3" open={!refused}>
                <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
                  <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden />
                  Generated SQL
                </summary>
                <SqlBlock sql={r.sql} className="mt-1.5" />
              </details>
            )}
            {r.columns && r.rows && r.rows.length > 0 && <ResultTable columns={r.columns} rows={r.rows} />}
          </>
        )}
      </Card>
    </div>
  )
}

const MAX_ROWS = 50

function ResultTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  const numeric = columns.map((_, c) =>
    rows.every((row) => row[c] == null || (typeof row[c] !== 'boolean' && row[c] !== '' && !Number.isNaN(Number(row[c])))),
  )
  return (
    <div className="mt-3 max-h-80 overflow-auto rounded-md border" tabIndex={0} role="region" aria-label="Query result">
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 bg-secondary">
          <tr>
            {columns.map((c, i) => (
              <th key={c} scope="col" className={cn('px-2.5 py-1.5 text-left font-mono font-semibold', numeric[i] && 'text-right')}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, MAX_ROWS).map((row, r) => (
            <tr key={r} className="border-t">
              {row.map((v, c) => (
                <td key={c} className={cn('px-2.5 py-1.5 font-mono', numeric[c] && 'text-right tabular-nums')}>
                  {v == null ? <span className="text-muted-foreground">null</span> : String(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > MAX_ROWS && (
        <p className="border-t px-2.5 py-1.5 text-xs text-muted-foreground">
          Showing {MAX_ROWS} of {rows.length} rows.
        </p>
      )}
    </div>
  )
}
