'use client'

import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { api } from '@/lib/api'
import type { DocumentMeta } from '@/lib/types'

export function DocumentViewer({ documentId, label }: { documentId: string; label?: string }) {
  const [meta, setMeta] = useState<DocumentMeta | null>(null)
  const [failed, setFailed] = useState(false)
  const [page, setPage] = useState(1)
  const [loaded, setLoaded] = useState<number | null>(null)

  useEffect(() => {
    api.document(documentId).then(setMeta, () => setFailed(true))
  }, [documentId])

  const pages = meta?.pages ?? 1
  return (
    <Card className="p-2.5 lg:sticky lg:top-3">
      <div className="flex items-center justify-between gap-2 px-1 pb-2 text-xs text-muted-foreground">
        <div className="flex items-center gap-1">
          {pages > 1 && (
            <Button
              variant="ghost"
              size="sm"
              className="size-7 px-0"
              aria-label="Previous page"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              <ChevronLeft />
            </Button>
          )}
          <span>
            Page {page} of {pages}
          </span>
          {pages > 1 && (
            <Button
              variant="ghost"
              size="sm"
              className="size-7 px-0"
              aria-label="Next page"
              disabled={page >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight />
            </Button>
          )}
        </div>
        <span className="truncate font-mono">{label ?? meta?.filename}</span>
      </div>
      {failed ? (
        <p className="grid aspect-[1/1.3] place-items-center rounded-md border text-sm text-muted-foreground">
          The document preview isn&apos;t available.
        </p>
      ) : (
        <div className="relative aspect-[1/1.3] overflow-hidden rounded-md border bg-white max-lg:max-h-[420px]">
          {loaded !== page && <div className="absolute inset-0 animate-pulse bg-secondary" />}
          {/* eslint-disable-next-line @next/next/no-img-element -- served by the API with the session cookie */}
          <img
            key={page}
            src={api.pageUrl(documentId, page)}
            alt={`Page ${page} of ${meta?.filename ?? 'the document'}`}
            className="block w-full"
            onLoad={() => setLoaded(page)}
            onError={() => setFailed(true)}
          />
        </div>
      )}
    </Card>
  )
}
