'use client'

import { AlertTriangle, FileUp, Loader2, ScanLine } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Notice, RateLimitNotice } from '@/components/notices'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ApiError, api, startRunWithFallback, type Limits } from '@/lib/api'
import { amount, docTypeLabel } from '@/lib/format'
import type { Sample } from '@/lib/types'
import { cn } from '@/lib/utils'

export function StartPanel() {
  const router = useRouter()
  const [samples, setSamples] = useState<Sample[] | null>(null)
  const [limits, setLimits] = useState<Limits | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [retryAt, setRetryAt] = useState<number | null>(null)

  useEffect(() => {
    api.samples().then(setSamples, (e: ApiError) => setLoadError(e.message))
    api.limits().then(setLimits, () => setLimits(null))
  }, [])

  // Links like /#upload (from a finished PO run): jump again once the cards have rendered,
  // since they arrive after the browser's own jump and push the section down.
  useEffect(() => {
    if (!samples) return
    const id = window.location.hash.slice(1)
    if (id === 'samples' || id === 'upload') {
      requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ block: 'start' }))
    }
  }, [samples])

  const start = useCallback(
    async (key: string, getDocument: () => Promise<{ document_id: string }>) => {
      setBusy(key)
      setError(null)
      try {
        const doc = await getDocument()
        const run = await startRunWithFallback(doc.document_id)
        const query = new URLSearchParams({ doc: doc.document_id })
        if (run.fallback) query.set('fallback', run.fallback)
        router.push(`/runs/${run.run_id}?${query}`)
      } catch (e) {
        const err = e instanceof ApiError ? e : new ApiError(0, 'unknown', String(e))
        if (err.code === 'rate_limited') setRetryAt(Date.now() + (err.retryAfterSeconds ?? 60) * 1000)
        else setError(err)
        setBusy(null)
      }
    },
    [router],
  )

  const blocked = busy !== null || retryAt !== null
  const sorted = samples
    ? [...samples].sort((a, b) => Number(a.doc_type === 'invoice') - Number(b.doc_type === 'invoice'))
    : null

  return (
    <>
      <div className="space-y-3" aria-live="polite">
        {retryAt !== null && <RateLimitNotice until={retryAt} onDone={() => setRetryAt(null)} />}
        {error && <Notice tone="danger" title="That didn't work">{error.message}</Notice>}
      </div>

      <div id="samples" className="mb-3 mt-8 flex scroll-mt-4 flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">Try a sample</h2>
        <p className="text-[13px] text-muted-foreground">
          Process the POs first so the invoices have something to match against.
        </p>
      </div>

      {loadError ? (
        <Notice tone="danger" title="Couldn't load the samples">{loadError}</Notice>
      ) : (
        <ul className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {sorted
            ? sorted.map((s) => (
                <li key={s.id}>
                  <SampleCard
                    sample={s}
                    busy={busy === s.id}
                    disabled={blocked}
                    onStart={() => start(s.id, () => api.sampleDocument(s.id))}
                  />
                </li>
              ))
            : Array.from({ length: 6 }, (_, i) => (
                <li key={i} className="h-[186px] animate-pulse rounded-lg border bg-secondary/60" />
              ))}
        </ul>
      )}

      <h2 id="upload" className="mb-3 mt-10 scroll-mt-4 text-lg font-semibold">
        Or upload your own
      </h2>
      <UploadBox
        limits={limits}
        busy={busy === 'upload'}
        disabled={blocked}
        onFile={(file) => start('upload', () => api.upload(file))}
      />
    </>
  )
}

function SampleCard({
  sample: s,
  busy,
  disabled,
  onStart,
}: {
  sample: Sample
  busy: boolean
  disabled: boolean
  onStart: () => void
}) {
  const mismatch = s.expected_match === 'mismatch'
  const isInvoice = s.doc_type === 'invoice'
  const label = `${docTypeLabel(s.doc_type)} ${s.number} from ${s.party ?? 'unknown'}${mismatch ? ', PO mismatch' : ''}${s.scanned ? ', scanned' : ''}`
  return (
    <button
      type="button"
      onClick={onStart}
      disabled={disabled}
      title={`Process ${label}`}
      className={cn(
        'group block w-full overflow-hidden rounded-lg border bg-card text-left transition-colors hover:border-primary focus-visible:border-primary disabled:cursor-not-allowed',
        mismatch && 'border-destructive/60',
        disabled && !busy && 'opacity-60',
      )}
    >
      <div
        className={cn(
          'flex h-[100px] items-end gap-2 border-b bg-secondary px-3 pb-2.5',
          s.scanned && 'grayscale',
        )}
        aria-hidden
      >
        <div
          className={cn(
            'flex h-[84px] w-16 flex-col gap-1 rounded-[3px] border border-gray-200 bg-white p-2 shadow-sm',
            s.scanned && '-rotate-2',
          )}
        >
          <i className="block h-[3px] w-3/5 rounded bg-gray-300" />
          {Array.from({ length: Math.min(s.lines + 2, 6) }, (_, i) => (
            <i key={i} className="block h-[3px] rounded bg-gray-300" />
          ))}
        </div>
        {busy && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Starting…
          </span>
        )}
      </div>
      <div className="px-3.5 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={isInvoice ? 'invoice' : 'po'}>{docTypeLabel(s.doc_type)}</Badge>
          {s.scanned && (
            <Badge>
              <ScanLine /> Scanned
            </Badge>
          )}
          {mismatch && (
            <Badge variant="danger">
              <AlertTriangle /> PO mismatch
            </Badge>
          )}
          <span className="font-mono text-xs text-muted-foreground">{s.number}</span>
        </div>
        <div className="mt-1.5 font-medium">{s.party}</div>
        <div className="text-xs text-muted-foreground">
          {isInvoice
            ? `against ${s.references_po} · ${s.expected_match === 'partial' ? 'partial delivery · ' : ''}${s.currency} ${amount(s.total)}`
            : `${s.lines} lines · ${s.currency} ${amount(s.total)} · ${s.template} layout`}
        </div>
      </div>
    </button>
  )
}

function UploadBox({
  limits,
  busy,
  disabled,
  onFile,
}: {
  limits: Limits | null
  busy: boolean
  disabled: boolean
  onFile: (file: File) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const mb = limits ? Math.round(limits.max_upload_bytes / 1024 / 1024) : null
  const off = limits?.replay_only ?? false
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled && !off) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const file = e.dataTransfer.files[0]
        if (file && !disabled && !off) onFile(file)
      }}
      className={cn(
        'flex flex-col items-start justify-between gap-4 rounded-lg border-[1.5px] border-dashed p-6 sm:flex-row sm:items-center',
        over && 'border-primary bg-primary/5',
      )}
    >
      <div>
        <div className="font-medium">Drop a PO or invoice here</div>
        <div className="mt-1 flex flex-wrap gap-x-3 text-[13px] text-muted-foreground">
          {limits ? (
            <>
              <span>{limits.file_types.join(', ')}</span>
              <span>· up to {mb} MB</span>
              <span>· up to {limits.max_pages} pages</span>
              <span>
                · {limits.runs_per_window} runs per {limits.window_minutes} min
              </span>
            </>
          ) : (
            <span className="inline-block h-4 w-72 animate-pulse rounded bg-secondary" />
          )}
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          {off
            ? 'Uploads are paused while the demo replays recorded runs.'
            : `Please don't upload real documents with personal data — uploads are deleted after ${limits?.session_ttl_hours ?? 24} h.`}
        </p>
      </div>
      <input
        ref={input}
        type="file"
        accept="application/pdf,image/png,image/jpeg"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (file) onFile(file)
        }}
      />
      <Button variant="outline" disabled={disabled || off} onClick={() => input.current?.click()}>
        {busy ? <Loader2 className="animate-spin" /> : <FileUp />}
        {busy ? 'Uploading…' : 'Choose file'}
      </Button>
    </div>
  )
}
