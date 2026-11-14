import type { Metadata } from 'next'

import { RunView } from '@/components/run/run-view'

export const metadata: Metadata = { title: 'Run · DocAgent' }

export default async function RunPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>
  searchParams: Promise<{ fallback?: string; doc?: string }>
}) {
  const { id } = await params
  const { fallback, doc } = await searchParams
  return <RunView key={id} id={id} fallback={fallback ?? null} documentHint={doc ?? null} />
}
