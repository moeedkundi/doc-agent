import { ArrowDown, ExternalLink } from 'lucide-react'
import type { Metadata } from 'next'

import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { REPO_URL, repoFile } from '@/lib/site'
import { cn } from '@/lib/utils'

export const metadata: Metadata = { title: 'Architecture · DocAgent' }

const SERVERS = [
  {
    name: 'extraction',
    port: 8101,
    llm: true,
    summary:
      'Reads the document with a vision model through the gateway. Every value comes back with a confidence, source page and evidence snippet. The model gets no tools, so text in a document cannot trigger actions.',
    tools: [
      ['classify_document', 'purchase order, invoice or unknown'],
      ['extract_fields', 'typed record with per-field confidence'],
      ['reextract_fields', 're-read only the flagged fields (strong tier)'],
    ],
  },
  {
    name: 'validation',
    port: 8102,
    llm: false,
    summary:
      'Deterministic checks and the PO ↔ invoice two-way match in pure Python: no LLM, no database, no network. The same input always gives the same answer.',
    tools: [
      ['validate_record', 'arithmetic, dates, formats, low confidence'],
      ['match_invoice_to_po', 'line pairing, price/qty tolerance, remaining value'],
      ['list_rules', 'the rule catalogue'],
    ],
  },
  {
    name: 'sqlstore',
    port: 8103,
    llm: false,
    summary:
      'Postgres access. Reads go through a SELECT-only pipeline under a read-only role with row-level security and a timeout; writes only through typed, parameterised tools.',
    tools: [
      ['get_schema', 'tables and columns the reader can see'],
      ['run_select', 'one guarded SELECT, row-capped'],
      ['save_record', 'typed insert of a reviewed record'],
      ['get_purchase_order', 'PO plus earlier invoices against it'],
      ['save_match_result', 'typed insert of a match result'],
    ],
  },
  {
    name: 'reference',
    port: 8104,
    llm: false,
    summary:
      'Static reference data bundled with the server: vendor master, illustrative FX rates and tax rates. No external APIs, so the demo has no live dependency here.',
    tools: [
      ['lookup_vendor', 'fuzzy name / tax-ID match against the vendor master'],
      ['convert_currency', 'static illustrative rates'],
      ['lookup_tax_rate', 'standard / reduced VAT and sales tax'],
    ],
  },
] as const

const INGEST = ['classify', 'extract', 'validate', 'reextract', 'human_review', 'validate_final', 'save', 'po_match', 'summarize']

const DECISIONS = [
  {
    title: 'Deterministic validation, not an LLM judge',
    text: 'Arithmetic, dates, formats and PO matching are plain Python rules with tests. They are cheap, reproducible and auditable, and a model cannot talk its way past them. The LLM only reads the document; deciding whether the numbers add up is code.',
  },
  {
    title: 'Selective re-extraction instead of full retries',
    text: 'When validation flags fields, only those field paths go back to the stronger (and pricier) model tier. Most documents never escalate, and the ones that do pay for a few fields, not a whole page. If the strong tier still isn’t sure, a human decides.',
  },
  {
    title: 'Row-level security for isolation',
    text: 'Every visitor gets an anonymous session; every query runs with the session id set and Postgres RLS policies filter by it. Model-written SQL runs as a separate read-only role with a statement timeout, so even a bad query can neither see another session’s data nor change anything.',
  },
  {
    title: 'Replay mode',
    text: 'The six samples have recorded runs. When the daily LLM budget is used up or live processing is off, the demo replays them with their original timing instead of failing — the public site keeps working at zero marginal cost.',
  },
  {
    title: 'MCP servers as separate processes',
    text: 'Each capability is its own service with its own credentials: of the tools, only sqlstore touches the database and only extraction sends a document to a model. The agent gets per-node tool allowlists (classify and extract cannot reach sqlstore), so a prompt-injected document has nothing dangerous to call.',
  },
  {
    title: 'What changes on-prem',
    text: 'Swap the hosted model for a vision-language model on a local GPU behind the same gateway, and drop all external APIs. The MCP boundaries, validation, review flow, RLS and evals stay as they are; SSO, an audit log and PII redaction would be added.',
  },
]

export default function ArchitecturePage() {
  return (
    <div className="container pb-10 pt-8">
      <h1 className="text-2xl font-bold tracking-tight">Architecture</h1>
      <p className="mt-1.5 max-w-3xl text-sm text-muted-foreground">
        A LangGraph agent drives four MCP servers. Every model call goes through one cost-aware
        gateway; every step is written to a trace the UI streams live.
      </p>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <ExtLink href={REPO_URL}>GitHub repository</ExtLink>
        <ExtLink href={repoFile('SPEC.md')}>Design spec</ExtLink>
      </div>

      <section aria-labelledby="diagram-title" className="mt-7">
        <h2 id="diagram-title" className="text-lg font-semibold">
          System diagram
        </h2>
        <SystemDiagram />
      </section>

      <section aria-labelledby="servers-title" className="mt-9">
        <h2 id="servers-title" className="text-lg font-semibold">
          MCP servers
        </h2>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Streamable HTTP on a private network, each request authenticated with a shared secret.
          Never exposed publicly.
        </p>
        <div className="mt-3 grid gap-3.5 md:grid-cols-2">
          {SERVERS.map((s) => (
            <Card key={s.name} className="flex flex-col p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-mono text-sm font-semibold">{s.name}</h3>
                <Badge variant={s.llm ? 'invoice' : 'po'}>{s.llm ? 'LLM via gateway' : 'no LLM'}</Badge>
                <span className="font-mono text-xs text-muted-foreground">:{s.port}</span>
              </div>
              <p className="mt-1.5 text-[13px] text-muted-foreground">{s.summary}</p>
              <ul className="mt-3 space-y-1 text-[13px]">
                {s.tools.map(([tool, what]) => (
                  <li key={tool} className="flex flex-wrap gap-x-2">
                    <code className="rounded bg-secondary px-1.5 font-mono text-xs leading-5">{tool}</code>
                    <span className="text-muted-foreground">{what}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-auto pt-3 text-sm">
                <ExtLink href={repoFile(`servers/${s.name}/README.md`)}>README</ExtLink>
              </div>
            </Card>
          ))}
        </div>
      </section>

      <section aria-labelledby="decisions-title" className="mt-9">
        <h2 id="decisions-title" className="text-lg font-semibold">
          Key design decisions
        </h2>
        <div className="mt-3 grid gap-3.5 md:grid-cols-2 lg:grid-cols-3">
          {DECISIONS.map((d) => (
            <Card key={d.title} className="p-4">
              <h3 className="text-sm font-semibold">{d.title}</h3>
              <p className="mt-1 text-[13px] text-muted-foreground">{d.text}</p>
            </Card>
          ))}
        </div>
      </section>
    </div>
  )
}

function ExtLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} className="inline-flex items-center gap-1 text-primary hover:underline dark:text-violet-300">
      {children}
      <ExternalLink className="size-3.5" aria-hidden />
    </a>
  )
}

function Box({ title, sub, className, children }: { title: string; sub?: string; className?: string; children?: React.ReactNode }) {
  return (
    <div className={cn('rounded-lg border bg-card px-3 py-2.5', className)}>
      <div className="text-sm font-semibold">{title}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      {children}
    </div>
  )
}

function Down({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-1.5 py-1.5 text-[11px] text-muted-foreground">
      <ArrowDown className="size-3.5" aria-hidden />
      <span>{label}</span>
    </div>
  )
}

function SystemDiagram() {
  return (
    <figure className="mt-3 rounded-lg border bg-secondary/40 p-3 sm:p-5">
      <div className="mx-auto max-w-4xl">
        <Box title="Browser · Next.js" sub="landing, live trace, review form, ask, evals" className="mx-auto max-w-md text-center" />
        <Down label="HTTPS · JSON + server-sent events · session cookie" />
        <Box title="FastAPI" sub="sessions, uploads, rate limits, budget checks, SSE from the trace table, replay" className="mx-auto max-w-xl text-center" />
        <Down label="starts / resumes graph runs" />
        <div className="grid gap-3 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <Box title="LangGraph agent" sub="per-node tool allowlists · Postgres checkpointer · trace of every step">
            <ol className="mt-2 flex flex-wrap items-center gap-1 font-mono text-[11px]">
              {INGEST.map((n, i) => (
                <li key={n} className="flex items-center gap-1">
                  <span className={cn('rounded border px-1.5 py-px', n === 'reextract' && 'border-warning/60 bg-warning-soft', n === 'human_review' && 'border-primary/50 bg-primary/10')}>
                    {n}
                  </span>
                  {i < INGEST.length - 1 && <span className="text-muted-foreground" aria-hidden>→</span>}
                </li>
              ))}
            </ol>
            <p className="mt-1.5 font-mono text-[11px] text-muted-foreground">
              ask: plan SQL → run_select → (fix SQL ≤ 2×) → answer
            </p>
          </Box>
          <Box title="LLM gateway" sub="LiteLLM · default + strong tier · retries · daily, session and eval budget caps · cost ledger">
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Badge variant="po">Gemini · default tier</Badge>
              <Badge variant="warning">Gemini · strong tier</Badge>
            </div>
          </Box>
        </div>
        <Down label="MCP over Streamable HTTP · private network · shared secret" />
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {SERVERS.map((s) => (
            <Box key={s.name} title={s.name} sub={s.llm ? 'vision LLM via gateway' : 'no LLM'} className="text-center font-mono" />
          ))}
        </div>
        <Down label="SQL (sqlstore, API, checkpointer, ledger)" />
        <Box
          title="Postgres 16"
          sub="row-level security per session · read-only reader role for model SQL · LangGraph checkpoints · trace events · cost ledger"
          className="mx-auto max-w-2xl text-center"
        />
      </div>
      <figcaption className="sr-only">
        The browser talks to a FastAPI service, which runs a LangGraph agent. The agent calls four
        MCP servers (extraction, validation, sqlstore, reference) and makes model calls only
        through the LLM gateway. Data lives in Postgres with row-level security.
      </figcaption>
    </figure>
  )
}
