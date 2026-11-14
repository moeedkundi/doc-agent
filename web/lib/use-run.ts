'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, api } from './api'
import type { EndEvent, IngestResult, ReviewPayload, Run, RunStatus, TraceEvent } from './types'

const TRACE_KINDS = ['node_start', 'tool_end', 'node_end', 'error', 'interrupt'] as const

export type Connection = 'connecting' | 'live' | 'reconnecting' | 'closed' | 'lost'

export interface RunState {
  run: Run | null
  loadError: ApiError | null
  events: TraceEvent[]
  status: RunStatus | null
  review: ReviewPayload | null
  result: IngestResult | null
  errorCode: string | null
  connection: Connection
}

/** A run's state plus its live SSE trace. EventSource reconnects on its own and sends
 * Last-Event-ID, so the API only replays what was missed. */
export function useRun(id: string) {
  const [state, setState] = useState<RunState>({
    run: null,
    loadError: null,
    events: [],
    status: null,
    review: null,
    result: null,
    errorCode: null,
    connection: 'connecting',
  })
  const seen = useRef(new Set<number>())

  useEffect(() => {
    let source: EventSource | null = null
    let cancelled = false
    seen.current = new Set()

    api.run(id).then(
      (run) => {
        if (cancelled) return
        setState((s) => ({
          ...s,
          run,
          status: s.status ?? run.status,
          review: run.review,
          result: run.result,
          errorCode: run.error?.code ?? null,
        }))
        source = new EventSource(api.eventsUrl(id), { withCredentials: true })
        source.onopen = () => setState((s) => ({ ...s, connection: 'live' }))
        source.onerror = () => {
          if (!source) return
          const lost = source.readyState === EventSource.CLOSED
          setState((s) =>
            s.connection === 'closed' ? s : { ...s, connection: lost ? 'lost' : 'reconnecting' },
          )
        }
        for (const kind of TRACE_KINDS) {
          source.addEventListener(kind, (msg) => {
            const event = JSON.parse((msg as MessageEvent<string>).data) as TraceEvent
            if (seen.current.has(event.seq)) return
            seen.current.add(event.seq)
            setState((s) => ({ ...s, events: [...s.events, event] }))
          })
        }
        source.addEventListener('status', (msg) => {
          const { status } = JSON.parse((msg as MessageEvent<string>).data) as { status: RunStatus }
          setState((s) => ({
            ...s,
            status,
            review: status === 'awaiting_review' ? s.review : null,
          }))
        })
        source.addEventListener('review_required', (msg) => {
          const review = JSON.parse((msg as MessageEvent<string>).data) as ReviewPayload | null
          setState((s) => ({ ...s, review: review ?? s.review }))
        })
        source.addEventListener('end', (msg) => {
          const end = JSON.parse((msg as MessageEvent<string>).data) as EndEvent
          source?.close()
          setState((s) => ({
            ...s,
            status: end.status,
            result: end.result,
            errorCode: end.error_code,
            review: null,
            connection: 'closed',
          }))
          // Pick up the final row (cost, finished_at, error message).
          api.run(id).then((run) => !cancelled && setState((s) => ({ ...s, run })), () => {})
        })
      },
      (err: unknown) => {
        if (cancelled) return
        const e = err instanceof ApiError ? err : new ApiError(0, 'unknown', String(err))
        setState((s) => ({ ...s, loadError: e, connection: 'closed' }))
      },
    )
    return () => {
      cancelled = true
      source?.close()
    }
  }, [id])

  const submitReview = useCallback(
    async (decision: { action: 'approve' | 'edit'; record?: unknown }) => {
      await api.resume(id, decision)
      setState((s) => ({ ...s, review: null, status: 'running' }))
    },
    [id],
  )

  return { ...state, submitReview }
}
