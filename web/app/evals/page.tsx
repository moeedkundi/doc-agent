import type { Metadata } from 'next'

import { EvalsView } from '@/components/evals/evals-view'

export const metadata: Metadata = { title: 'Evals · DocAgent' }

export default function EvalsPage() {
  return (
    <div className="container pb-10 pt-8">
      <h1 className="text-2xl font-bold tracking-tight">Evals</h1>
      <p className="mt-1.5 max-w-3xl text-sm text-muted-foreground">
        Every document in the eval set goes through the same pipeline as the demo — classify,
        extract, validate, review (auto-approved), save, PO match — and each field is scored
        against ground truth.
      </p>
      <EvalsView />
    </div>
  )
}
