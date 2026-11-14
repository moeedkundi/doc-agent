import { StartPanel } from '@/components/start-panel'

const FLOW = [
  { title: 'Document', text: 'PDF or image upload, or a sample', tools: [] },
  { title: 'MCP tools', text: 'Classify, extract, validate', tools: ['extract_fields', 'validate_record'] },
  { title: 'Review', text: 'Low-confidence fields pause for you', tools: [] },
  { title: 'Database', text: 'Typed save + PO match', tools: ['save_record', 'match_invoice_to_po'] },
  { title: 'Ask', text: 'Questions → read-only SQL', tools: ['run_select'] },
]

export default function Home() {
  return (
    <div className="container pb-8">
      <section className="pb-6 pt-10 sm:pt-14">
        <p className="inline-flex rounded-full border border-primary/30 bg-primary/10 px-2.5 py-0.5 text-xs font-semibold text-primary dark:text-violet-300">
          Live demo · synthetic documents only
        </p>
        <h1 className="mt-4 text-3xl font-bold leading-tight tracking-tight sm:text-4xl">
          Turn invoices and purchase orders into{' '}
          <span className="gradient-text">checked, queryable data</span>.
        </h1>
        <p className="mt-3 max-w-3xl text-base text-muted-foreground sm:text-lg">
          An agent reads the document through MCP tools, validates it, asks a human when it
          isn&apos;t sure, saves it, and matches invoices to their PO — every step traced with its
          model, tokens and cost.
        </p>

        <h2 className="sr-only">How it works</h2>
        <ol className="mt-7 grid gap-5 md:grid-cols-5 md:gap-6">
          {FLOW.map((step, i) => (
            <li
              key={step.title}
              className="relative rounded-lg border bg-card p-3.5 after:absolute after:text-muted-foreground max-md:after:-bottom-5 max-md:after:left-1/2 max-md:after:-translate-x-1/2 max-md:after:content-['↓'] md:after:-right-5 md:after:top-1/2 md:after:-translate-y-1/2 md:after:content-['→'] last:after:content-none"
            >
              <span className="text-[11px] font-semibold text-primary dark:text-violet-300">{i + 1}</span>
              <h3 className="text-sm font-semibold">{step.title}</h3>
              <p className="mt-0.5 text-[13px] text-muted-foreground">{step.text}</p>
              {step.tools.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {step.tools.map((t) => (
                    <code key={t} className="rounded bg-secondary px-1.5 py-px font-mono text-xs">
                      {t}
                    </code>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
      </section>

      <StartPanel />
    </div>
  )
}
