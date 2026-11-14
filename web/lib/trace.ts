// Groups a run's SSE events into timeline steps (one per graph node) with running totals.

import type { TraceEvent, Usage } from './types'

export type StepStatus = 'running' | 'done' | 'error' | 'paused'

export interface Step {
  key: string
  node: string
  status: StepStatus
  calls: TraceEvent[]
  end: TraceEvent | null
  error: string | null
  strong: boolean
}

function usageOf(e: TraceEvent): Usage | null {
  const p = e.payload
  if (p && typeof p === 'object' && 'usage' in p) {
    const u = (p as { usage: unknown }).usage
    if (u && typeof u === 'object') return u as Usage
  }
  return null
}

export function isStrongCall(e: TraceEvent): boolean {
  if (usageOf(e)?.tier === 'strong') return true
  const args = e.args as Record<string, unknown> | null
  return !!args && typeof args === 'object' && args.model_tier === 'strong'
}

export function buildSteps(events: TraceEvent[]): Step[] {
  const steps: Step[] = []
  const open = new Map<string, Step>()
  for (const e of events) {
    let step = open.get(e.node)
    if (e.kind === 'node_start' && step?.status === 'paused') {
      // A resumed node (human review) starts again: continue its row instead of adding one.
      step.status = 'running'
      continue
    }
    if (e.kind === 'node_start' || !step) {
      step = {
        key: `${e.node}-${e.seq}`,
        node: e.node,
        status: 'running',
        calls: [],
        end: null,
        error: null,
        strong: e.node === 'reextract',
      }
      steps.push(step)
      open.set(e.node, step)
      if (e.kind === 'node_start') continue
    }
    if (e.kind === 'tool_end') {
      step.calls.push(e)
      if (isStrongCall(e)) step.strong = true
    } else if (e.kind === 'node_end') {
      step.end = e
      step.status = 'done'
      open.delete(e.node)
    } else if (e.kind === 'error') {
      step.end = e
      step.error = e.error
      step.status = 'error'
      open.delete(e.node)
    } else if (e.kind === 'interrupt') {
      step.status = 'paused'
    }
  }
  return steps
}

export interface StepTotals {
  cost: number
  tokens: number
  latencyMs: number | null
  model: string | null
}

/** A finished step's node_end carries its totals; while running, sum the calls so far. */
export function stepTotals(step: Step): StepTotals {
  const llmCalls = step.calls.filter((c) => c.model)
  const summed = {
    cost: step.calls.reduce((s, c) => s + (c.cost_usd ?? 0), 0),
    tokens: step.calls.reduce((s, c) => s + (c.tokens_in ?? 0) + (c.tokens_out ?? 0), 0),
  }
  const end = step.end
  return {
    cost: end?.cost_usd ?? summed.cost,
    tokens: end && (end.tokens_in != null || end.tokens_out != null)
      ? (end.tokens_in ?? 0) + (end.tokens_out ?? 0)
      : summed.tokens,
    latencyMs: end?.latency_ms ?? null,
    model: llmCalls.at(-1)?.model ?? null,
  }
}

export function runTotals(steps: Step[]): { cost: number; tokens: number; toolCalls: number } {
  let cost = 0
  let tokens = 0
  let toolCalls = 0
  for (const s of steps) {
    const t = stepTotals(s)
    cost += t.cost
    tokens += t.tokens
    toolCalls += s.calls.length
  }
  return { cost, tokens, toolCalls }
}

/** The latest extraction payload's per-field confidences, keyed by field path. */
export function confidences(events: TraceEvent[]): Map<string, number> {
  const out = new Map<string, number>()
  const walk = (v: unknown, path: string) => {
    if (Array.isArray(v)) v.forEach((x, i) => walk(x, `${path}[${i}]`))
    else if (v && typeof v === 'object') {
      const o = v as Record<string, unknown>
      if ('value' in o && typeof o.confidence === 'number') out.set(path, o.confidence)
      else for (const [k, x] of Object.entries(o)) walk(x, path ? `${path}.${k}` : k)
    }
  }
  for (const e of events) {
    if (e.kind !== 'tool_end' || !e.payload || typeof e.payload !== 'object') continue
    const p = e.payload as Record<string, unknown>
    if (e.tool === 'extract_fields' && p.record) walk(p.record, '')
    if (e.tool === 'reextract_fields' && p.fields && typeof p.fields === 'object') {
      // {"line_items[1].unit_price": {value, confidence, ...}}
      for (const [path, f] of Object.entries(p.fields as Record<string, unknown>)) {
        const c = (f as { confidence?: unknown } | null)?.confidence
        if (typeof c === 'number') out.set(path, c)
      }
    }
  }
  return out
}
