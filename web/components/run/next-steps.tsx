'use client'

import { ArrowDown, ArrowRight, FileUp, LayoutGrid, Loader2 } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { Notice } from '@/components/notices'
import { Button, buttonVariants } from '@/components/ui/button'
import { ApiError, api, startRunWithFallback } from '@/lib/api'
import type { Sample } from '@/lib/types'

/** A floating "results below" button while `targetId` is off screen below the viewport. */
export function ScrollHint({ targetId, label }: { targetId: string; label: string }) {
  const [below, setBelow] = useState(false)
  useEffect(() => {
    const el = document.getElementById(targetId)
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => setBelow(!!entry && !entry.isIntersecting && entry.boundingClientRect.top > 0),
      { rootMargin: '0px 0px -80px 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [targetId])
  if (!below) return null
  return (
    <button
      type="button"
      onClick={() => document.getElementById(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
      className="fixed bottom-5 left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg transition-transform hover:scale-105"
    >
      {label}
      <ArrowDown className="size-4 animate-bounce" aria-hidden />
    </button>
  )
}

/** After a purchase order: point the visitor at the invoice that matches it. */
export function PoNextStep({ poNumber }: { poNumber: string | null }) {
  const router = useRouter()
  const [invoice, setInvoice] = useState<Sample | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!poNumber) return
    api.samples().then(
      (samples) =>
        setInvoice(samples.find((s) => s.doc_type === 'invoice' && s.references_po === poNumber) ?? null),
      () => setInvoice(null),
    )
  }, [poNumber])

  const runInvoice = async () => {
    if (!invoice) return
    setBusy(true)
    setError(null)
    try {
      const doc = await api.sampleDocument(invoice.id)
      const run = await startRunWithFallback(doc.document_id)
      const query = new URLSearchParams({ doc: doc.document_id })
      if (run.fallback) query.set('fallback', run.fallback)
      router.push(`/runs/${run.run_id}?${query}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <Notice tone="info" title="Next: process an invoice for this PO">
      <p>
        The purchase order is saved. Now run an invoice that references{' '}
        <span className="font-mono">{poNumber ?? 'it'}</span>: the agent will match it line by line
        and show any mismatch side by side.
      </p>
      <div className="mt-2.5 flex flex-wrap gap-2">
        {invoice && (
          <Button size="sm" onClick={runInvoice} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}
            Run its invoice <span className="font-mono">{invoice.number}</span>
          </Button>
        )}
        <Link href="/#samples" className={buttonVariants({ size: 'sm', variant: 'outline' })}>
          <LayoutGrid /> Back to samples
        </Link>
        <Link href="/#upload" className={buttonVariants({ size: 'sm', variant: 'outline' })}>
          <FileUp /> Upload an invoice
        </Link>
      </div>
      {error && <p className="mt-2 text-danger-foreground">{error}</p>}
    </Notice>
  )
}
