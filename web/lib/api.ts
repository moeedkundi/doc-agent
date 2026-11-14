// Client for the DocAgent API. Every call sends the session cookie (SameSite=Lax: the web
// app and the API must be on the same site, e.g. localhost:3000 + localhost:8000).

import type { DocumentMeta, EvalReport, Run, Sample } from './types'

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfterSeconds: number | null = null,
    readonly replayAvailable = false,
  ) {
    super(message)
  }
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch(`${API_URL}${path}`, { credentials: 'include', ...init })
  } catch {
    throw new ApiError(0, 'network', "Can't reach the DocAgent API. Is it running?")
  }
  if (!res.ok) throw await toError(res)
  return res
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await (await send(path, init)).json()) as T
}

async function toError(res: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {}
  try {
    body = (await res.json()) as Record<string, unknown>
  } catch {
    // not JSON (proxy error page etc.)
  }
  const header = Number(res.headers.get('Retry-After'))
  const retry =
    typeof body.retry_after_seconds === 'number'
      ? body.retry_after_seconds
      : Number.isFinite(header) && header > 0
        ? header
        : null
  return new ApiError(
    res.status,
    typeof body.error === 'string' ? body.error : `http_${res.status}`,
    typeof body.message === 'string' ? body.message : `Request failed (${res.status}).`,
    retry,
    body.replay_available === true,
  )
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export interface Limits {
  max_upload_bytes: number
  max_pages: number
  file_types: string[]
  runs_per_window: number
  window_minutes: number
  session_ttl_hours: number
  replay_only: boolean
}

export interface StartedRun {
  run_id: string
  replay: boolean
}

export const api = {
  samples: () => request<Sample[]>('/api/samples'),
  limits: () => request<Limits>('/api/limits'),
  sampleDocument: (id: string) =>
    request<DocumentMeta>(`/api/samples/${encodeURIComponent(id)}/document`, { method: 'POST' }),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DocumentMeta>('/api/documents', { method: 'POST', body: form })
  },
  document: (id: string) => request<DocumentMeta>(`/api/documents/${id}`),
  pageUrl: (id: string, page: number) => `${API_URL}/api/documents/${id}/pages/${page}`,
  startRun: (documentId: string, replay = false) =>
    request<StartedRun>('/api/runs', json({ document_id: documentId, replay })),
  run: (id: string) => request<Run>(`/api/runs/${id}`),
  eventsUrl: (id: string) => `${API_URL}/api/runs/${id}/events`,
  /** Streams the ask run's events (SSE over a POST response). */
  ask: (question: string) => send('/api/ask', json({ question })),
  evals: () => request<EvalReport>('/api/evals/latest'),
  resume: (id: string, decision: { action: 'approve' | 'edit'; record?: unknown }) =>
    request<{ run_id: string; status: string }>(`/api/runs/${id}/resume`, json(decision)),
}

/** Start a run; if live processing is unavailable (budget used up, replay-only mode) and the
 * document has a recording, fall back to replaying it. */
export async function startRunWithFallback(
  documentId: string,
): Promise<StartedRun & { fallback: string | null }> {
  try {
    return { ...(await api.startRun(documentId)), fallback: null }
  } catch (err) {
    if (
      err instanceof ApiError &&
      err.replayAvailable &&
      (err.code === 'budget_exceeded' || err.code === 'replay_only')
    ) {
      try {
        return { ...(await api.startRun(documentId, true)), fallback: err.code }
      } catch (replayErr) {
        // No recording for this document (an upload): report the original reason.
        if (replayErr instanceof ApiError && replayErr.code === 'no_recording') throw err
        throw replayErr
      }
    }
    throw err
  }
}
